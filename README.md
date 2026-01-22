# Traffic Gen
## 🌐 Distributed Traffic Simulator V3.0 (Docker Swarm + Ansible)

這是一個基於 Playwright 的擬真流量模擬系統，設計用於在 Docker Swarm 叢集上大規模運行。系統具備自動化部署能力，能夠模擬真實人類的瀏覽行為（滑動、點擊、看影片、下載），並支援動態設定檔掛載。

## 🏗️ 系統架構
**硬體配置**
* Control Node: Windows 11 (WSL 2 - Ubuntu)
* Cluster Nodes: 6 台 Ubuntu 24.04 VM (VMware NAT)
    * Manager: 1 台 (負責調度)
    * Worker: 5 台 (負責跑流量)
    * 規格: 每台 6 vCPU / 8GB RAM / 30GB SSD

**軟體堆疊**
* 核心程式: Python + Playwright (Headless Chromium)
* 容器化: Docker + Docker Swarm
* 自動化部署: Ansible
* 版本控制: Git
## 📂 專案結構Traffic-Gen/
```
├── .gitattributes          # 強制定義 Linux 換行格式 (避免 Windows CRLF 問題)
├── .gitignore              # 忽略 venv 與暫存檔
│
├── src/                    # [程式碼核心]
│   ├── flow.py             # 模擬腳本
│   ├── sites.json          # 外部設定檔 (網址列表)
│   ├── requirements.txt    # Python 依賴
│   └── Dockerfile          # 建置 Image 用
│
└── deploy/                 # [部署核心]
    ├── inventory.ini       # Ansible 機器清單 (IP 與連線參數)
    ├── docker-stack.yml    # Swarm 服務定義
    ├── deploy_swarm.yml    # 自動化部署劇本 (Install -> Config -> Deploy)
    └── teardown_swarm.yml  # 銷毀叢集劇本 (一鍵清除)
```
## 🚀 Phase 1: 環境準備 (WSL Control Node)
**1. 安裝必要工具**
在 WSL 終端機執行：
```bash=
sudo apt update
sudo apt install -y ansible sshpass python3-venv
```
**2. 設定 SSH 金鑰互信**
Ansible 需要免密碼登入所有節點。
```bash=
# 產生金鑰 (一路 Enter)
ssh-keygen -t rsa -b 4096

# 發送給所有節點 (請替換為實際 IP)
ssh-copy-id manager@172.24.75.101
ssh-copy-id worker1@172.24.75.102
# ... (對 worker2 ~ worker5 重複執行)
```
3. **解決 WSL/VMware 網路問題**
若遇到 `Connection timed out` 或 `Banner exchange` 錯誤，通常是 MTU 問題。
```bash=
# 在 WSL 內暫時修改 MTU (每次重開機需重設，或寫入設定檔)
sudo ip link set dev eth0 mtu 1350
```

## 🛠️ Phase 2: 建置與發布 (Build & Push)
當修改了 `src/flow.py` 或依賴時，需重新打包映像檔。
```bash=
cd ~/Traffic-Gen
# 1. 建置映像檔 (注意路徑是 ./src)
docker build -t jhancc0118/traffic-generation:v3 ./src

# 2. 推送到 Docker Hub
docker push jhancc0118/traffic-generation:v3
```
## ⚡ Phase 3: 自動化部署 (Deploy)

**1. 配置 `inventory.ini`**
確保 deploy/inventory.ini 內的 IP 正確，並包含以下關鍵優化參數以解決連線逾時問題：
```bash=
[all:vars]
# 強制指定 Python3 (解決 /usr/bin/python not found)
ansible_python_interpreter=/usr/bin/python3
# 優化 SSH 連線，避免併發時 Timeout
ansible_ssh_common_args='-o StrictHostKeyChecking=no -o ControlMaster=auto -o ControlPersist=60s -o ConnectTimeout=120 -o ConnectionAttempts=10'
```
**2. 執行部署劇本**
此劇本會自動安裝 Docker、設定防火牆、掛載設定檔至 `/srv/traffic-bot` 並啟動服務。
```bash=
cd ~/Traffic-Gen/deploy

# -K: 輸入 sudo 密碼
# -f 5: 設定併發數 (若網路不穩可改為 -f 2)
ansible-playbook -i inventory.ini deploy_swarm.yml \
  -e "docker_hub_user=<Docker Username>" \
  -e "docker_hub_pass=<Login Token>" \
  -K -f 5
```

## 📊 監控與維運 (Monitoring)
**1. 服務狀態監控 (Manager Node)**
SSH 進入 Manager 節點查看整體狀況。
```bash=
# 查看服務總覽 (確認 Replicas 是否達到目標，例如 50/50)
docker service ls

# 查看容器分佈 (加上過濾只看 Running，畫面較乾淨)
docker service ps -f "desired-state=running" my-simulation_traffic-bot
```
**2. 日誌監控 (Log Monitoring)**
在 Manager Node 上可以直接查看所有機器人的行為匯總。
```bash=
# -f: 持續追蹤
# --tail 100: 只看最後 100 行
docker service logs -f --tail 100 my-simulation_traffic-bot
```
**3. 資源用量監控 (Worker Nodes)**
如果要檢查特定 Worker 是否過載（CPU/RAM）。
* **方法 A：登入單台 Worker 查看 (最詳細)**
```bash=
ssh traffic-gen-1@172.24.75.102
# 查看即時容器資源 (CPU/RAM)
docker stats
# 查看系統整體負載 (推薦安裝 htop)
htop
```
* **方法 B：使用 Ansible 批次查看 (上帝視角)** 在 WSL 終端機一次檢查所有機器的負載 (Load Average)。
```bash=
# 在 deploy 目錄執行
ansible all -m shell -a "uptime" -i inventory.ini
```
判斷標準: Load Average 若長時間大於 CPU 核心數 (6.0)，代表過載。

## ⚖️ 調整規模 (Scaling)
**方法一：修改設定檔並重新部署 (推薦)**
這是最正規的做法，確保設定檔與實際狀態一致。
1. 修改 `deploy/docker-stack.yml` 中的 `replicas` 數值。
2. 重新執行 Ansible 部署指令：
```bash=
ansible-playbook -i inventory.ini deploy_swarm.yml \
  -e "docker_hub_user=<Docker Username>" \
  -e "docker_hub_pass=<Login Token>" \
  -K -f 5
```
Swarm 會自動執行滾動更新，不中斷服務。
**方法二：臨時指令調整 (快速)**
若只是想短暫測試壓力，可直接在 Manager 下指令：
```bash=
docker service scale my-simulation_traffic-bot=100
```
**方法三：強制重新平衡 (Rebalance)**
若發現容器集中在某幾台機器，可強制重啟分配。
```bash=
docker service update --force my-simulation_traffic-bot
```
## ⚙️ 進階配置與調校 (Optimization)
目前針對 **6 vCPU / 8GB RAM** 的最佳化配置 (`docker-stack.yml`)：
**1. 規模設定**
* Replicas: 50 (平均每台 Worker 跑 10 個容器)
* Update Strategy: `parallelism: 10` (一次更新一台機器的量)，`order: stop-first` (先關再開，保護記憶體)。

**2. 資源限制**
```bash=
resources:
    limits:
        cpus: '1.0'        # 允許突發到 1 核
        memory: 1200M      # 上限 1.2G
    reservations:
        cpus: '0.55'
        memory: 600M       # [建議] 預留 600M 比較安全，避免單節點過載
```
**3. 硬碟保護**
使用 `tmpfs` 將暫存與下載目錄掛載到記憶體，避免大量下載損耗 SSD。
```bash=
tmpfs:
  - /tmp
```
## 🔧 常見問題排除 (Troubleshooting)
**Q1: Ansible 報錯** `banner exchange timeout` 或 `UNREACHABLE`
* **原因:** 網路 MTU 過大被丟包，或 SSH 併發過高導致擁塞。
* **解法:** 調低 WSL MTU: `sudo ip link set dev eth0 mtu 1350`
    * 降低 Ansible 併發: 加參數 -f 1 (一台一台做)。
    * Worker 端關閉 SSH DNS 反查: UseDNS no。

**Q2: 部署時卡在** `Could not get lock /var/lib/dpkg/...`
* **原因:** 虛擬機剛開機正在跑自動更新。
* **解法:** `deploy_swarm.yml` 已內建 `retries: 10` 機制，Ansible 會自動等待鎖定釋放。

**Q3: 服務啟動後，Worker 4/5 沒有分配到容器**
* **原因:** 部署時 Worker 還沒 Join 成功，Manager 就已經派發完任務。
* **解法:** `deploy_swarm.yml` 已修正順序，將 `stack deploy` 移至最後一步。若發生，可手動執行 `docker service update --force my-simulation_traffic-bot`。

## 🛑 停止或暫停服務 (Stop/Pause)
若不需銷毀整個叢集，僅想停止目前的模擬任務，請 SSH 進入 **Manager Node** 執行：

**方式 A：暫停模擬 (保留服務設定)**
將容器數量降為 0，容器會停止，但服務設定保留，隨時可 scale 回來。
```bash=
docker service scale my-simulation_traffic-bot=0
```
**方式 B：移除模擬任務 (釋放資源)**
這會刪除 Service 與 Stack 設定，停止所有容器，但節點仍留在 Swarm 中。
```bash=
docker stack rm my-simulation
```
## 🧨 叢集銷毀
若需重置所有節點（清除 Docker、離開 Swarm、刪除檔案）：
```bash=
cd ~/Traffic-Gen/deploy
ansible-playbook -i inventory.ini delete_swarm.yml -K
```

