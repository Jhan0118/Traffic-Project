import subprocess
import time
import os
import sys
import datetime
import getpass  # 用來安全輸入密碼
import re       # [新增] 用於解析 Docker 輸出

# ================= 設定區 =================
THRESHOLD_GB = 2.0 
MAX_ROUNDS = 1
TARGET_REPLICAS = 50

INVENTORY_PATH = "../deploy/inventory.ini"
PLAYBOOK_DIR = "./playbooks"
DATA_LAKE_DIR = "../data_lake"
SERVICE_NAME = "my-simulation_traffic-bot"

# 全域變數用來存密碼
# [資安重點] 絕對不要將密碼寫死在這裡！請保持為空字串或使用環境變數。
# 邏輯：優先讀取環境變數 -> 若無則在 main() 詢問使用者輸入
SUDO_PASSWORD = os.getenv('ANSIBLE_BECOME_PASS', "")
# ==========================================

def run_cmd(cmd, shell=False, check=True, print_output=False):
    """執行系統指令的 Helper Function"""
    try:
        # 除錯用：印出指令 (隱藏密碼)
        # cmd_debug = " ".join(cmd) if isinstance(cmd, list) else cmd
        # print(f"   [DEBUG] Executing: {cmd_debug.replace(SUDO_PASSWORD, '******')}")

        result = subprocess.run(cmd, shell=shell, check=check, 
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if print_output and result.stdout:
            print(f"      [Remote Output]:\n{result.stdout.strip()}")
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return None

def get_ansible_base_cmd(hosts_pattern, module="shell", args=None):
    """
    [核心函式] 產生帶有 sudo 密碼的 Ansible 指令
    解決你提到的問題：確保每一次呼叫都自動帶上 password
    """
    cmd = [
        "ansible", hosts_pattern, 
        "-i", INVENTORY_PATH, 
        "-b",  # become (sudo)
        # [關鍵修正] 這裡自動注入密碼，解決 manager node 執行 docker 指令需要 sudo 的問題
        "--extra-vars", f"ansible_become_pass={SUDO_PASSWORD}"
    ]
    
    if module:
        cmd.extend(["-m", module])
    
    if args:
        cmd.extend(["-a", args])
        
    return cmd

def get_playbook_cmd(playbook_path):
    """產生帶有密碼的 Playbook 指令"""
    return [
        "ansible-playbook", 
        "-i", INVENTORY_PATH, 
        playbook_path,
        "--extra-vars", f"ansible_become_pass={SUDO_PASSWORD}"
    ]

def get_max_file_size_gb():
    # 查詢檔案大小 (Workers)
    cmd = get_ansible_base_cmd("workers", args="stat -c %s /tmp/traffic_data/*.pcap 2>/dev/null || echo 0")
    output = run_cmd(cmd, check=False)
    
    max_size = 0.0
    if output:
        lines = output.split('\n')
        for line in lines:
            line = line.strip()
            if line.isdigit():
                size_bytes = int(line)
                size_gb = size_bytes / (1024**3)
                if size_gb > max_size:
                    max_size = size_gb
    return max_size

def verify_service_status(target_replicas, retry_times=6):
    print(f"   [Check] Verifying Docker Service status (Target: {target_replicas})...")
    
    # [修正] 移除 --format 參數，避免 Ansible Jinja2 解析錯誤 (unexpected '.')
    # 直接抓取完整輸出，再用正規表達式解析 Replicas 欄位 (格式: 0/50)
    cmd = get_ansible_base_cmd("managers", args=f"docker service ls --filter name={SERVICE_NAME}")

    for i in range(retry_times):
        output = run_cmd(cmd, check=False)
        if not output:
            print(f"      [Warn] Service not found yet...")
        else:
            # 使用 Regex 尋找 "數字/數字" 的模式 (例如 0/50, 50/50)
            # 這樣比依賴固定欄位更可靠
            match = re.search(r'\s(\d+)/(\d+)', output)
            
            if match:
                current = match.group(1)
                desired = match.group(2)
                status_str = f"{current}/{desired}"
                print(f"      -> Docker Attempt {i+1}/{retry_times}: {status_str}")
                
                # 解析成功，檢查數值
                if target_replicas > 0:
                    if int(current) > 0: return True
                else:
                    if int(current) == 0: return True
            else:
                # 如果找不到模式，印出原始輸出以便除錯 (但忽略 Ansible 成功的標頭雜訊)
                if "FAILED" in output or "error" in output.lower():
                     print(f"      -> Docker Attempt {i+1}/{retry_times}: Ansible Error. Raw Output:\n{output}")
                else:
                     print(f"      -> Docker Attempt {i+1}/{retry_times}: Parsing failed (No 'X/Y' found).")

        time.sleep(5)
    
    if target_replicas > 0:
        print(f"   [ERROR] Docker Service failed to reach target replicas!")
        return False
    return True

def verify_capture_status(retry_times=3):
    print(f"   [Check] Verifying tcpdump status on all workers...")
    
    # [修正] 加上 || true 防止 pgrep 沒找到時回傳 exit code 1 導致 Ansible 報錯 (FAILED)
    # 這樣 run_cmd 就不會拋出異常，我們會收到空字串，邏輯判斷依然正確
    cmd = get_ansible_base_cmd("workers", args="pgrep -f tcpdump || true")

    for i in range(retry_times):
        output = run_cmd(cmd, check=True) 
        if output:
            print(f"      -> Tcpdump is RUNNING on all nodes. (PIDs detected)")
            return True
        else:
            print(f"      -> Attempt {i+1}/{retry_times}: Tcpdump process NOT found...")
            time.sleep(2)
            
    print(f"   [CRITICAL] Tcpdump failed to start on one or more workers!")
    return False

def cleanup_on_exit():
    """
    [新增] 緊急清理函式
    當程式發生意外中斷 (Ctrl+C 或 Error) 時執行
    """
    print("\n\n!!! INTERRUPTED !!! Performing EMERGENCY CLEANUP...")
    
    print("   [Emergency] 1. Stopping traffic...")
    try:
        run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}=0"), check=False)
    except Exception as e:
        print(f"      Warning: Failed to stop docker: {e}")

    print("   [Emergency] 2. Stopping tcpdump on workers...")
    try:
        # 使用 stop_and_fetch 來確保 tcpdump 被殺掉
        run_cmd(get_playbook_cmd(f"{PLAYBOOK_DIR}/stop_and_fetch.yml"), check=False)
    except Exception as e:
        print(f"      Warning: Failed to stop tcpdump: {e}")
        
    print("!!! CLEANUP COMPLETE. System should be safe. !!!\n")

def main():
    global SUDO_PASSWORD
    print(f"=== Auto-Traffic-Pipeline Started ===")
    
    # 1. 密碼處理邏輯
    if not SUDO_PASSWORD:
        try:
            SUDO_PASSWORD = getpass.getpass("請輸入 sudo 密碼 (for Ansible become): ")
        except EOFError:
            print("[Error] 無法讀取輸入。若在非互動模式下執行，請設定環境變數 ANSIBLE_BECOME_PASS")
            sys.exit(1)

    if not SUDO_PASSWORD:
        print("[Error] 密碼不能為空！")
        sys.exit(1)

    print(f"[*] Threshold Config: Any node > {THRESHOLD_GB} GB")
    print(f"[*] Total Rounds: {MAX_ROUNDS}")
    print(f"[*] Target Replicas: {TARGET_REPLICAS}")
    
    os.makedirs(DATA_LAKE_DIR, exist_ok=True)

    try:
        # [關鍵修正] 使用 try...except...finally 包裹主要邏輯
        for round_id in range(1, MAX_ROUNDS + 1):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"\n>>> [Round {round_id}/{MAX_ROUNDS}] Start Time: {timestamp}")

            # --- Step 1: 啟動錄製 (Tcpdump) ---
            print("   [Action 1/4] Starting tcpdump on all workers...")
            run_cmd(get_playbook_cmd(f"{PLAYBOOK_DIR}/start_capture.yml"))

            if not verify_capture_status():
                print("   [ABORT] Capture failed. Stopping pipeline.")
                raise RuntimeError("Tcpdump start failed") # 觸發 finally

            # --- Step 2: 啟動流量 (Docker Scale Up) ---
            print(f"   [Action 2/4] Scaling UP traffic simulation (Replicas={TARGET_REPLICAS})...")
            
            run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}={TARGET_REPLICAS}"))

            if not verify_service_status(target_replicas=TARGET_REPLICAS):
                print("   [CRITICAL] Simulation failed to start. Aborting.")
                raise RuntimeError("Docker start failed") # 觸發 finally
            
            # --- Step 3: 監控迴圈 ---
            print("   [Monitor] Systems GREEN. Recording traffic...")
            start_time = time.time()
            
            while True:
                max_gb = get_max_file_size_gb()
                elapsed = int(time.time() - start_time)
                status_msg = f"\r      -> Max File Size: {max_gb:.4f} GB / {THRESHOLD_GB} GB (Elapsed: {elapsed}s)"
                sys.stdout.write(status_msg)
                sys.stdout.flush()

                if max_gb >= THRESHOLD_GB:
                    print(f"\n   [Trigger] Threshold reached! Stopping simulation.")
                    break
                time.sleep(10)

            # --- Step 4: 暫停流量 (Scale Down) ---
            print("   [Action 3/4] Scaling DOWN to 0 (Stopping traffic)...")
            run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}=0"))

            verify_service_status(target_replicas=0, retry_times=3)
            print("      -> Waiting 10s for connections to close...")
            time.sleep(10)

            # --- Step 5: 收割資料 ---
            print("   [Action 4/4] Stopping tcpdump and fetching files...")
            run_cmd(get_playbook_cmd(f"{PLAYBOOK_DIR}/stop_and_fetch.yml"))
            
            # --- Step 6: 本地檔案整理 ---
            print("   [Organize] Renaming files with timestamp...")
            count = 0
            for filename in os.listdir(DATA_LAKE_DIR):
                if filename.endswith(".pcap") and not filename.startswith("round"):
                    old_path = os.path.join(DATA_LAKE_DIR, filename)
                    new_name = f"round{round_id}_{timestamp}_{filename}"
                    new_path = os.path.join(DATA_LAKE_DIR, new_name)
                    try:
                        os.rename(old_path, new_path)
                        count += 1
                    except OSError:
                        pass
            
            print(f"   [Done] Round {round_id} completed. {count} files processed.")
            print("      -> Cooling down for 10s...")
            time.sleep(10)

        print("\n=== All Rounds Completed Successfully. Data is ready in data_lake/ ===")

    except KeyboardInterrupt:
        print("\n\n[User Abort] Detecting Ctrl+C...")
        cleanup_on_exit()
        sys.exit(1)
    except Exception as e:
        print(f"\n\n[Error] Unexpected error occurred: {e}")
        cleanup_on_exit()
        sys.exit(1)

if __name__ == "__main__":
    main()