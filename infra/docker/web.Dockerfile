FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS build

ARG NEXT_PUBLIC_API_URL=http://localhost:8001
WORKDIR /workspace/apps/web
RUN npm install --global npm@11.6.2 --no-audit --no-fund
COPY --chown=node:node apps/web/package.json apps/web/package-lock.json ./
RUN chown node:node /workspace/apps/web
USER node
RUN npm ci --no-audit --no-fund
COPY --chown=node:node apps/web/ ./
RUN npm run build && npm prune --omit=dev --omit=peer --no-audit --no-fund

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS runtime
ARG NEXT_PUBLIC_API_URL=http://localhost:8001
WORKDIR /workspace/apps/web
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL} \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production
COPY --from=build --chown=node:node /workspace/apps/web/package.json ./package.json
COPY --from=build --chown=node:node /workspace/apps/web/node_modules ./node_modules
COPY --from=build --chown=node:node /workspace/apps/web/.next ./.next
COPY --from=build --chown=node:node /workspace/apps/web/next.config.mjs ./next.config.mjs
USER node

CMD ["npm", "run", "start", "--", "--hostname", "0.0.0.0", "--port", "3000"]
