FROM nginx:1.27-alpine
COPY apps/web /usr/share/nginx/html
COPY services/web/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
