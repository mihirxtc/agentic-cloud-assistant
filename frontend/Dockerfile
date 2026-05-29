# Stage 1 — build
FROM node:20-alpine AS builder

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Relative URL so the built JS works on any host without hardcoded addresses
ARG VITE_MCP_URL=/mcp
ENV VITE_MCP_URL=$VITE_MCP_URL

RUN npm run build


# Stage 2 — serve
# Drops Node entirely; final image is ~25MB
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
