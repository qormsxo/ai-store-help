FROM node:24-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

EXPOSE 5173

# 개발용 이미지: 소스는 compose에서 볼륨으로 마운트되고 Vite HMR로 반영된다.
CMD ["npm", "run", "dev"]
