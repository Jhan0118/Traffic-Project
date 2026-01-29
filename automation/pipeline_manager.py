import subprocess
import time
import os
import sys
import datetime
import getpass
import re

# ================= 路徑設定區 (改用絕對路徑) =================
# 取得目前腳本所在的絕對路徑 (例如 /home/user/Traffic-Project/automation)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 定義專案根目錄 (automation 的上一層)
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)

# 重新定義關鍵路徑，確保不會迷路
INVENTORY_PATH = os.path.join(PROJECT_ROOT, "deploy", "inventory.ini")
PLAYBOOK_DIR = os.path.join(SCRIPT_DIR, "playbooks")
DATA_LAKE_DIR = os.path.join(PROJECT_ROOT, "data_lake")

SERVICE_NAME = "my-simulation_traffic-bot"
# ========================================================

# ================= 預設參數區 =================
DEFAULT_THRESHOLD_GB = 2.0 
DEFAULT_MAX_ROUNDS = 1
DEFAULT_TARGET_REPLICAS = 50

# 全域變數
THRESHOLD_GB = DEFAULT_THRESHOLD_GB
MAX_ROUNDS = DEFAULT_MAX_ROUNDS
TARGET_REPLICAS = DEFAULT_TARGET_REPLICAS
SUDO_PASSWORD = os.getenv('ANSIBLE_BECOME_PASS', "")
# ==========================================

def get_input_value(prompt, default_val, cast_type=str):
    try:
        user_input = input(f"{prompt} [預設 {default_val}]: ").strip()
        if not user_input:
            return default_val
        return cast_type(user_input)
    except ValueError:
        print(f"[Error] 輸入格式錯誤！")
        sys.exit(1)

def run_cmd(cmd, shell=False, check=True, print_output=False):
    """一般指令執行"""
    try:
        result = subprocess.run(cmd, shell=shell, check=check, 
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if print_output and result.stdout:
            print(f"      [Remote Output]:\n{result.stdout.strip()}")
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

def run_cmd_stream(cmd, description="Executing"):
    """
    [增強版] 串流指令執行 (即時顯示進度 + 錯誤細節)
    """
    print(f"   [{description}] Processing... (Please wait)")
    
    process = subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        universal_newlines=True
    )

    for line in process.stdout:
        line = line.strip()
        if not line: continue
        
        # 1. 顯示步驟名稱 (TASK)
        if "TASK [" in line:
             # 解析 TASK 名稱
             try:
                 task_name = line.split('TASK [')[-1].split(']')[0]
             except IndexError:
                 task_name = line
             
             if "警告" in task_name or "debug" in task_name:
                 continue
             print(f"      -> Step: {task_name}")

        # 2. 顯示成功/變更狀態
        elif "ok: [" in line or "changed: [" in line:
             print(f"      -> Status Update: {line}")

        # 3. [增強] 捕捉所有可能的錯誤關鍵字
        # 包含 fatal (嚴重錯誤), failed (任務失敗), unreachable (連線失敗), error (一般錯誤)
        elif any(k in line.lower() for k in ["fatal:", "failed:", "unreachable:", "error", "rsync error"]):
             print(f"      [!] Error Detail: {line}")
        
        # 4. [增強] 捕捉 Ansible 的詳細錯誤訊息 (通常在 JSON 的 msg 欄位)
        elif "msg\":" in line or "\"msg\":" in line:
             print(f"      [!] Error Message: {line}")

    return_code = process.wait()
    if return_code != 0:
        return False
    return True

def get_ansible_base_cmd(hosts_pattern, module="shell", args=None):
    cmd = [
        "ansible", hosts_pattern, 
        "-i", INVENTORY_PATH, 
        "-b",
        "--extra-vars", f"ansible_become_pass={SUDO_PASSWORD}"
    ]
    if module:
        cmd.extend(["-m", module])
    if args:
        cmd.extend(["-a", args])
    return cmd

def get_playbook_cmd(playbook_path):
    return [
        "ansible-playbook", 
        "-i", INVENTORY_PATH, 
        playbook_path,
        "--extra-vars", f"ansible_become_pass={SUDO_PASSWORD}"
    ]

def get_max_file_size_gb():
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
    cmd = get_ansible_base_cmd("managers", args=f"docker service ls --filter name={SERVICE_NAME}")
    for i in range(retry_times):
        output = run_cmd(cmd, check=False)
        if not output:
            print(f"      [Warn] Service not found yet...")
        else:
            match = re.search(r'\s(\d+)/(\d+)', output)
            if match:
                current = match.group(1)
                desired = match.group(2)
                print(f"      -> Docker Attempt {i+1}/{retry_times}: {current}/{desired}")
                if target_replicas > 0:
                    if int(current) > 0: return True
                else:
                    if int(current) == 0: return True
            else:
                 if "FAILED" in output or "error" in output.lower():
                     print(f"      -> Docker Attempt {i+1}/{retry_times}: Ansible Error.")
                 else:
                     print(f"      -> Docker Attempt {i+1}/{retry_times}: Parsing failed.")
        time.sleep(5)
    if target_replicas > 0:
        print(f"   [ERROR] Docker Service failed to reach target replicas!")
        return False
    return True

def verify_capture_status(retry_times=3):
    print(f"   [Check] Verifying tcpdump status on all workers...")
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
    print("\n\n!!! INTERRUPTED !!! Performing EMERGENCY CLEANUP...")
    
    # 1. 停止流量 (不需要進度條，因為是單一指令)
    print("   [Emergency] 1. Stopping traffic...")
    try:
        run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}=0"), check=False)
    except Exception: pass

    # 2. 停止錄製並取回檔案 (需要進度條，因為這是最花時間的步驟)
    print("   [Emergency] 2. Stopping tcpdump on workers...")
    try:
        # [修改] 改用 run_cmd_stream 以顯示即時進度 (例如: changed: [worker1])
        run_cmd_stream(get_playbook_cmd(os.path.join(PLAYBOOK_DIR, "stop_and_fetch.yml")), description="Emergency Fetch")
    except Exception: pass
    
    print("!!! CLEANUP COMPLETE. System should be safe. !!!\n")

def main():
    global SUDO_PASSWORD, THRESHOLD_GB, MAX_ROUNDS, TARGET_REPLICAS
    
    print(f"=== Auto-Traffic-Pipeline Started ===")
    
    # 輸入密碼
    if not SUDO_PASSWORD:
        try:
            SUDO_PASSWORD = getpass.getpass("1. 請輸入 sudo 密碼 (for Ansible): ")
        except EOFError: sys.exit(1)
    if not SUDO_PASSWORD: sys.exit(1)

    # 參數設定
    print("\n--- 設定參數 (按 Enter 使用預設值) ---")
    THRESHOLD_GB = get_input_value("2. 單一節點檔案大小上限 (GB)", DEFAULT_THRESHOLD_GB, float)
    MAX_ROUNDS = get_input_value("3. 總執行輪數 (Rounds)", DEFAULT_MAX_ROUNDS, int)
    TARGET_REPLICAS = get_input_value("4. 模擬容器數量 (Replicas)", DEFAULT_TARGET_REPLICAS, int)
    
    # 建立目錄 (如果不存在)
    os.makedirs(DATA_LAKE_DIR, exist_ok=True)
    
    # [新增] 除錯訊息：確認路徑
    print(f"\n[Debug] Data Lake Path: {DATA_LAKE_DIR}")

    try:
        for round_id in range(1, MAX_ROUNDS + 1):
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            print(f"\n>>> [Round {round_id}/{MAX_ROUNDS}] Start Time: {timestamp}")

            # Step 1: Start Capture
            print("   [Action 1/4] Starting tcpdump on all workers...")
            run_cmd(get_playbook_cmd(os.path.join(PLAYBOOK_DIR, "start_capture.yml")))
            if not verify_capture_status(): raise RuntimeError("Capture failed")

            # Step 2: Start Traffic
            print(f"   [Action 2/4] Scaling UP traffic simulation (Replicas={TARGET_REPLICAS})...")
            run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}={TARGET_REPLICAS}"))
            if not verify_service_status(target_replicas=TARGET_REPLICAS): raise RuntimeError("Docker start failed")
            
            # Step 3: Monitor
            print("   [Monitor] Systems GREEN. Recording traffic...")
            start_time = time.time()
            while True:
                max_gb = get_max_file_size_gb()
                elapsed = int(time.time() - start_time)
                sys.stdout.write(f"\r      -> Max File Size: {max_gb:.4f} GB / {THRESHOLD_GB} GB (Elapsed: {elapsed}s)")
                sys.stdout.flush()
                if max_gb >= THRESHOLD_GB:
                    print(f"\n   [Trigger] Threshold reached! Stopping simulation.")
                    break
                time.sleep(10)

            # Step 4: Stop Traffic
            print("   [Action 3/4] Scaling DOWN to 0 (Stopping traffic)...")
            run_cmd(get_ansible_base_cmd("managers", args=f"docker service scale {SERVICE_NAME}=0"))
            verify_service_status(target_replicas=0, retry_times=3)
            print("      -> Waiting 10s for connections to close...")
            time.sleep(10)

            # Step 5: Stop & Fetch
            print("   [Action 4/4] Stopping tcpdump and fetching files...")
            run_cmd_stream(get_playbook_cmd(os.path.join(PLAYBOOK_DIR, "stop_and_fetch.yml")), description="Downloading Data")
            
            # Step 6: Rename (修復版)
            print(f"   [Organize] scanning {DATA_LAKE_DIR} for files to rename...")
            count = 0
            
            # 確保目錄存在
            if not os.path.exists(DATA_LAKE_DIR):
                print(f"      [Error] Data Lake directory not found at: {DATA_LAKE_DIR}")
            else:
                files = os.listdir(DATA_LAKE_DIR)
                # print(f"      [Debug] Found files: {files}") # 需要詳細除錯可打開此行
                
                for filename in files:
                    if filename.endswith(".pcap") and not filename.startswith("round"):
                        old_path = os.path.join(DATA_LAKE_DIR, filename)
                        new_name = f"round{round_id}_{timestamp}_{filename}"
                        new_path = os.path.join(DATA_LAKE_DIR, new_name)
                        try:
                            os.rename(old_path, new_path)
                            print(f"      -> Renamed: {filename} -> {new_name}")
                            count += 1
                        except OSError as e:
                            print(f"      [!] Failed to rename {filename}: {e}")
            
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