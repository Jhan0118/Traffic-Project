# [選擇基底映像檔]
# 使用微軟官方 Playwright Python 環境 (v1.57.0)
FROM mcr.microsoft.com/playwright/python:v1.57.0-jammy

# [設定工作目錄]
WORKDIR /app

# [複製依賴清單]
# 先複製 requirements.txt，利用 Docker Layer Cache 機制加速建置
# 如果程式碼變了但依賴沒變，這一步就不會重跑
COPY requirements.txt .

# [安裝依賴]
RUN pip install --no-cache-dir -r requirements.txt

# [安裝瀏覽器]
# 確保 Chromium 安裝完整
RUN playwright install chromium

# [複製程式碼]
# 複製 src 資料夾內的所有檔案 (flow.py, sites.json 等) 到容器內
COPY . .

# [設定環境變數]
ENV HEADLESS_MODE=true
ENV PYTHONUNBUFFERED=1

# [建立掛載點目錄]
# ★ 這是為了 V3 架構新增的 ★
# 我們需要這個空資料夾，讓 Swarm 把 sites.json Config 掛載進來
RUN mkdir -p /traffic_data

# [啟動指令]
CMD ["python", "./flow.py"]