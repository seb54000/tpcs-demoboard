FROM node:20-alpine AS build

WORKDIR /app

ENV NPM_CONFIG_UPDATE_NOTIFIER=false \
    NPM_CONFIG_FUND=false

COPY package*.json ./
RUN npm ci --audit=false

ARG VITE_API_URL=/api
ARG VITE_ENABLE_WORKER=true
ENV VITE_API_URL=${VITE_API_URL}
ENV VITE_ENABLE_WORKER=${VITE_ENABLE_WORKER}

COPY . .
RUN npm run build -- --outDir dist

FROM nginx:1.25-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
