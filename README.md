# Auto-Traffic-Pipeline  
**企業級自動化流量模擬與資料收集系統**

> **Version**: 3.4 (Multi-Protocol & Fault-Tolerant)  
> **Maintainer**: Traffic-Project Team  
> **Status**: Production-Ready (For Simulation Environment)

---

## 📘 專案簡介

**Auto-Traffic-Pipeline** 是一個基於 **Docker Swarm** 與 **Python** 架構的高併發、高擬真網路流量生成系統。  
本系統可用於生成大規模、多協定（HTTP/S、FTP、SSH、SMB、SMTP）的網路流量數據集 (PCAP)，專為 **非監督式資安偵測 AI 模型 (Unsupervised Intrusion Detection Systems)** 訓練與驗證所設計。

### ✨ 核心特性
- 自動化佈署與全自動任務循環  
- 支援全天候流量錄製與多節點收集  
- 故障偵測與自我修復 (Retry)  
- 磁碟空間保護機制（自動清理過量資料）  
- 適合長期運行於虛擬化環境（如 Proxmox、ESXi、WSL2）

---

## 🏗 系統架構 (System Architecture)

採用 **控制層 (Control Plane)** 與 **執行層 (Data Plane)** 分離架構設計，確保流量生成穩定與資料完整。

| 節點角色 | 組件 / 服務 | 職責說明 |
| :--- | :--- | :--- |
| **Control Node** (Local/WSL) | `pipeline_manager.py`、Ansible | **指揮官**：控管生命週期、監控狀態、回收數據。<br>不生成流量，僅負責調度。 |
| **Manager Node** (Swarm Mgr) | Target Servers：Mail、FTP、SSH、SMB | **靶機**：部署各協定服務容器，模擬東西向流量 (East-West Traffic)。 |
| **Worker Nodes** (Swarm Workers) | Traffic Bots、`tcpdump` | **流量產生器**：執行高併發模擬機器人及封包錄製。 |

---

## ⚙️ 環境需求與前置作業 (Prerequisites)

### 1. 軟體依賴

| 節點 | 所需軟體 |
| :-- | :-- |
| **Control Node** | Python 3.8+、Ansible 2.9+、`rsync` |
| **Cluster Nodes** | Docker Engine 24+、Python 3 |

### 2. 硬體與配置建議
- **Worker 建議配置**：2 vCPU / 4GB RAM  
  ⚠️ 長時間錄製會造成高 I/O 與記憶體壓力，請監控 OOM。
- **磁碟空間**
  - Worker：至少 30GB（每輪錄製約 2GB）  
  - Control Node (WSL)：建議將儲存 pcap 檔的資料夾設至 Windows 實體磁碟（例如 `/mnt/d/`），避免 `ext4.vhdx` 膨脹
- **網路**
  - Control Node 需設定 SSH 免密登入各節點  
  - 節點間須為同區網，避免 Rsync 傳輸過慢

---

## 🚀部署與更新流程 (Deployment Workflow)
### 1. 設定 Inventory 檔案
編輯 `deploy/inventory.ini`：
```
text
[managers]
172.24.xx.xx ansible_user=traffic-gen

[workers]
172.24.xx.yy ansible_user=traffic-gen
```
## 2. 建置與推送映像
修改 `src/flow.py` 後重新打包與推送：

```bash
docker build -t <docker_user>/traffic-generation:<version> ./src
docker push <docker_user>/traffic-generation:<version>
```
注意：請同步更新 docker-stack.yml 及 deploy_swarm.yml 內的版本標籤。

### 3. 部署基礎設施
使用 Ansible 進行自動化佈署：

```bash
cd deploy
ansible-playbook -i inventory.ini deploy_swarm.yml \
  -K \
  -e "docker_hub_user=<USER>" \
  -e "docker_hub_pass=<TOKEN>"
```
## ▶️ 自動化模擬執行 (Execution)
啟動主控流程：

```bash
cd automation
python3 pipeline_manager.py
```
#### 自動化循環邏輯 (Automation Loop)
1. Scale Up：啟動 Swarm 流量容器，等待全部就緒
2. Start Capture：在 Worker 啟動 tcpdump
3. Monitor：每 10 秒偵測 PCAP 檔案大小
4. Threshold Reached：單檔達上限（如 2GB）即停止錄製
5. Scale Down：冷卻期 60 秒，釋放系統資源
6. Global Stop：確保存檔後關閉所有記錄器
7. Fetch Data：使用 Rsync 自動回收檔案，具 Retry 與自動清理機制
