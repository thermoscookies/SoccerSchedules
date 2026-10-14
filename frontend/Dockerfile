FROM node:22-alpine
WORKDIR /app
RUN npm install --global npm@10.9.2 --ignore-scripts --no-audit --no-fund
COPY package*.json ./
RUN npm ci --ignore-scripts --no-audit --no-fund
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
EXPOSE 3000
CMD ["npm", "run", "dev"]
