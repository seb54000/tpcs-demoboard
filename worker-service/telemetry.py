import json
import logging
import os
import sys
from datetime import datetime, timezone

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import get_current_span


SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "demoboard-worker")
SERVICE_VERSION = os.getenv("OTEL_SERVICE_VERSION", "0.1.0")
ENVIRONMENT = os.getenv("OTEL_ENVIRONMENT", "local")
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() == "true"
APP_LOG_LEVEL = os.getenv("APP_LOG_LEVEL", "INFO").upper()
APP_LOG_FILE = os.getenv("APP_LOG_FILE")
K8S_ENV_TO_ATTR = {
    "POD_NAME": "k8s.pod.name",
    "POD_NAMESPACE": "k8s.namespace.name",
    "POD_UID": "k8s.pod.uid",
    "NODE_NAME": "k8s.node.name",
    "DEPLOYMENT_NAME": "k8s.deployment.name",
    "CONTAINER_NAME": "k8s.container.name",
}
WORKER_JOB_DURATION_BUCKETS_MS = tuple(
    float(value)
    for value in os.getenv(
        "WORKER_JOB_DURATION_BUCKETS_MS",
        "100,250,500,750,1000,1250,1500,1750,2000,2250,2500,2750,3000,3500,4000,5000",
    ).split(",")
    if value.strip()
)


def _k8s_attributes() -> dict[str, str]:
    attributes = {}
    for env_name, attr_name in K8S_ENV_TO_ATTR.items():
        value = os.getenv(env_name)
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
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[metric_reader],
        views=[
            View(
                instrument_name="demoboard_worker_job_duration_ms",
                aggregation=ExplicitBucketHistogramAggregation(
                    boundaries=WORKER_JOB_DURATION_BUCKETS_MS
                ),
            )
        ],
    )
    metrics.set_meter_provider(meter_provider)

    configure_telemetry._configured = True


configure_telemetry()

tracer = trace.get_tracer(SERVICE_NAME)
meter = metrics.get_meter(SERVICE_NAME)
job_counter = meter.create_counter(
    "demoboard_worker_jobs_processed_total",
    description="Total number of jobs processed by the worker.",
)
job_duration = meter.create_histogram(
    "demoboard_worker_job_duration_ms",
    unit="ms",
    description="Time spent processing jobs in the worker.",
)
logger = logging.getLogger(SERVICE_NAME)
