FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json* /app/
RUN npm install --omit=dev

COPY index.js /app/index.js

# Baileys auth state persisted via volume mount to /app/auth
VOLUME ["/app/auth"]

CMD ["node", "index.js"]

