# Phase 1 dev image — vite preview on a baked build.
# The dev compose service (`ncmu-spa` in docker-compose.yml) does NOT use
# this Dockerfile; it pulls node:20-alpine and runs `vite dev` against a
# bind-mounted source tree. This image exists for ad-hoc previews and
# as the starting point for the Phase 6 prod build (which will switch
# the runtime to nginx serving the built static assets).
FROM node:20-alpine

WORKDIR /app

COPY package.json ./
RUN npm install

COPY . .

RUN npm run build

EXPOSE 4173
CMD ["npm", "run", "preview"]
