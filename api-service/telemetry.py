import json
import logging
import os
import sys
from datetime import datetime, timezone

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import get_current_span


SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "demoboard-api")
SERVICE_VERSION = os.getenv("OTEL_SERVICE_VERSION", "0.1.0")
ENVIRONMENT = os.getenv("OTEL_ENVIRONMENT", "local")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
OTEL_LOGS_ENABLED = os.getenv("OTEL_LOGS_ENABLED", "false").lower() == "true"
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").upper()
APP_LOG_FILE = os.getenv("APP_LOG_FILE")
NODE_ZONE_FILE = os.getenv("NODE_ZONE_FILE", "/var/run/demoboard/node_zone")
K8S_ENV_TO_ATTR = {
    "POD_NAME": "k8s.pod.name",
    "POD_NAMESPACE": "k8s.namespace.name",
    "POD_UID": "k8s.pod.uid",
    "NODE_NAME": "k8s.node.name",
    "NODE_ZONE": "k8s.node.zone",
    "DEPLOYMENT_NAME": "k8s.deployment.name",
    "CONTAINER_NAME": "k8s.container.name",
}


def _k8s_attributes() -> dict[str, str]:
    attributes = {}
    for env_name, attr_name in K8S_ENV_TO_ATTR.items():
        value = os.getenv(env_name)
        if not value and env_name == "NODE_ZONE":
            try:
                value = open(NODE_ZONE_FILE, "r", encoding="utf-8").read().strip()
            except OSError:
                value = ""
        if value:
            attributes[attr_name] = value
    return attributes


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        span = get_current_span()
        span_context = span.get_span_context()
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "service.name": SERVICE_NAME,
        }
        payload.update(_k8s_attributes())
        if span_context.is_valid:
            payload["trace_id"] = f"{span_context.trace_id:032x}"
            payload["span_id"] = f"{span_context.span_id:016x}"
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging() -> None:
    root_logger = logging.getLogger()
    if getattr(root_logger, "_demoboard_logging_configured", False):
        return

    formatter = JsonFormatter()
    handlers: list[logging.Handler] = []

    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setFormatter(formatter)
    handlers.append(stdout_handler)

    if APP_LOG_FILE:
        file_handler = logging.FileHandler(APP_LOG_FILE)
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)

    root_logger.handlers.clear()
    root_logger.setLevel(APP_LOG_LEVEL)
    for handler in handlers:
        root_logger.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(logger_name)
        logger.handlers.clear()
        logger.propagate = True

    root_logger._demoboard_logging_configured = True


def configure_telemetry() -> None:
    if getattr(configure_telemetry, "_configured", False):
        return

    configure_logging()

    if not OTEL_ENABLED:
        configure_telemetry._configured = True
        return

    resource_attributes = {
        "service.name": SERVICE_NAME,
        "service.version": SERVICE_VERSION,
        "deployment.environment": ENVIRONMENT,
    }
    resource_attributes.update(_k8s_attributes())
    resource = Resource.create(resource_attributes)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(),
        export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "5000")),
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    if OTEL_LOGS_ENABLED:
        logger_provider = LoggerProvider(resource=resource)
        logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
        set_logger_provider(logger_provider)
        logging.getLogger().addHandler(
            LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
        )

    configure_telemetry._configured = True


configure_telemetry()

tracer = trace.get_tracer(SERVICE_NAME)
meter = metrics.get_meter(SERVICE_NAME)
request_counter = meter.create_counter(
    "demoboard_api_requests_total",
    description="Total number of HTTP requests handled by the API.",
)
request_duration = meter.create_histogram(
    "demoboard_api_request_duration_ms",
    unit="ms",
    description="HTTP request latency observed by the API.",
)
task_create_counter = meter.create_counter(
    "demoboard_api_tasks_created_total",
    description="Total number of tasks created through the API.",
)
job_start_counter = meter.create_counter(
    "demoboard_api_jobs_started_total",
    description="Total number of async jobs enqueued by the API.",
)
logger = logging.getLogger(SERVICE_NAME)
