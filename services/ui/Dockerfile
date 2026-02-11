# UI Service - Static SPA served via nginx
FROM nginx:alpine

# Copy custom nginx config
COPY services/ui/nginx.conf /etc/nginx/conf.d/default.conf

# Copy static SPA files
COPY services/ui/public /usr/share/nginx/html

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD wget --no-verbose --tries=1 --spider http://localhost:8501/health || exit 1

EXPOSE 8501

CMD ["nginx", "-g", "daemon off;"]

