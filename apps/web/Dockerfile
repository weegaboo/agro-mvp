FROM node:20-alpine AS deps

WORKDIR /app/apps/web
COPY apps/web/package.json ./
RUN npm install

FROM node:20-alpine AS builder

WORKDIR /app
COPY --from=deps /app/apps/web/node_modules /app/apps/web/node_modules
COPY . /app

WORKDIR /app/apps/web
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
RUN npm run build
RUN mkdir -p /app/apps/web/public

FROM node:20-alpine AS runner

ENV NODE_ENV=production
ARG NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE_URL=${NEXT_PUBLIC_API_BASE_URL}
WORKDIR /app/apps/web

COPY --from=builder /app/apps/web/.next /app/apps/web/.next
COPY --from=builder /app/apps/web/public /app/apps/web/public
COPY --from=builder /app/apps/web/package.json /app/apps/web/package.json
COPY --from=builder /app/apps/web/node_modules /app/apps/web/node_modules
COPY --from=builder /app/apps/web/next.config.mjs /app/apps/web/next.config.mjs

EXPOSE 3000
CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
