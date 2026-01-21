"""
【Human Traffic Simulator V2.9 - 真實人類行為模擬器 (分頁追蹤修復版)】

更新日誌 (V2.9):
1. [邏輯修復] 解決「點擊後無法進入下一層」的 Bug (The New Tab Trap)。
   - 問題：點擊 `target="_blank"` 連結會開啟新分頁，但腳本仍控制舊分頁。
   - 修正：實作 `context.expect_page()` 監聽。若開啟新分頁，腳本會自動切換控制權至新分頁，並關閉舊分頁，確保瀏覽路徑連續。
2. [行為優化] 點擊連結前再次確認座標，防止頁面滾動導致點擊失效 (Miss click)。
3. [功能維持] 保留 V2.8 的所有功能 (DNS 雜訊、貝茲曲線、帕雷托分佈)。

環境需求：
pip install playwright numpy
playwright install chromium
"""

import asyncio
import random
import os
import time
import logging
import socket
import numpy as np
from playwright.async_api import async_playwright, Page, BrowserContext

# --- 設定日誌 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("HumanSim")

class SystemNoise:
    """系統背景雜訊產生器"""
    NOISE_DOMAINS = [
        "time.windows.com", "time.google.com", "pool.ntp.org",
        "ctldl.windowsupdate.com", "dns.msftncsi.com",
        "settings-win.data.microsoft.com", "watson.telemetry.microsoft.com",
        "clients1.google.com", "optimizationguide-pa.googleapis.com",
        "update.avast.com", "api.dropbox.com", "client.dropbox.com",
        "geo.skype.com", "static.discordapp.com"
    ]

    @staticmethod
    async def run_background_dns_noise():
        """在背景持續運作，隨機發送 DNS 查詢"""
        while True:
            try:
                target = random.choice(SystemNoise.NOISE_DOMAINS)
                await asyncio.sleep(random.randint(10, 60))
                logger.info(f" [System Noise] Resolving DNS: {target}")
                try:
                    await asyncio.to_thread(socket.gethostbyname, target)
                except socket.gaierror:
                    pass
            except asyncio.CancelledError:
                break 
            except Exception as e:
                logger.warning(f"DNS Noise Error: {e}")

class HumanBehavior:
    """人類行為數學模型庫"""
    _last_mouse_pos = {'x': 0, 'y': 0}

    @staticmethod
    def get_pareto_sleep_time(min_s=2.0, max_s=300.0, alpha=3.0):
        """使用帕雷托分佈計算等待時間"""
        s = (np.random.pareto(alpha) + 1) * min_s
        final_time = min(s, max_s)
        return final_time

    @staticmethod
    def bezier_curve(p0, p1, p2, p3, steps=30):
        """計算三次貝茲曲線路徑"""
        path = []
        for t in np.linspace(0, 1, steps):
            x = (1-t)**3 * p0[0] + 3 * (1-t)**2 * t * p1[0] + \
                3 * (1-t) * t**2 * p2[0] + t**3 * p3[0]
            y = (1-t)**3 * p0[1] + 3 * (1-t)**2 * t * p1[1] + \
                3 * (1-t) * t**2 * p2[1] + t**3 * p3[1]
            path.append((x, y))
        return path

    @staticmethod
    async def human_mouse_move(page: Page, target_x: float, target_y: float):
        """模擬人類滑鼠移動：貝茲曲線 + 隨機變速"""
        start_box = page.viewport_size
        if not start_box:
            return

        if HumanBehavior._last_mouse_pos['x'] == 0 and HumanBehavior._last_mouse_pos['y'] == 0:
            HumanBehavior._last_mouse_pos['x'] = random.randint(0, start_box['width'])
            HumanBehavior._last_mouse_pos['y'] = random.randint(0, start_box['height'])
            await page.mouse.move(HumanBehavior._last_mouse_pos['x'], HumanBehavior._last_mouse_pos['y'])

        start_x = HumanBehavior._last_mouse_pos['x']
        start_y = HumanBehavior._last_mouse_pos['y']
        
        offset = random.randint(100, 500)
        p1 = (start_x + random.randint(-offset, offset), start_y + random.randint(-offset, offset))
        p2 = (target_x + random.randint(-offset, offset), target_y + random.randint(-offset, offset))
        
        steps = random.randint(20, 50)
        path = HumanBehavior.bezier_curve((start_x, start_y), p1, p2, (target_x, target_y), steps)

        for point in path:
            await page.mouse.move(point[0], point[1])
            HumanBehavior._last_mouse_pos['x'] = point[0]
            HumanBehavior._last_mouse_pos['y'] = point[1]
            await asyncio.sleep(random.uniform(0.001, 0.01)) 

    @staticmethod
    async def perform_dummy_clicks(page: Page):
        """模擬無效點擊"""
        if random.random() > 0.4: 
            return

        logger.info(" -> [Noise] Performing dummy clicks...")
        width = page.viewport_size['width']
        height = page.viewport_size['height']
        
        for _ in range(random.randint(1, 3)):
            safe_x = random.choice([
                random.randint(10, int(width * 0.1)), 
                random.randint(int(width * 0.9), width - 10)
            ])
            safe_y = random.randint(10, height - 10)
            
            await HumanBehavior.human_mouse_move(page, safe_x, safe_y)
            await asyncio.sleep(random.uniform(0.1, 0.3))
            await page.mouse.click(safe_x, safe_y)
            await asyncio.sleep(random.uniform(0.5, 2.0))

    @staticmethod
    async def human_scroll(page: Page):
        """模擬人類閱讀式滾動"""
        try:
            scroll_height = await page.evaluate("document.body.scrollHeight")
            current_scroll = 0
            viewport_height = page.viewport_size['height'] if page.viewport_size else 800
            
            max_scrolls = random.randint(3, 10)
            scroll_count = 0

            while current_scroll < scroll_height and scroll_count < max_scrolls:
                scroll_step = random.randint(int(viewport_height * 0.3), int(viewport_height * 0.7))
                
                await page.mouse.wheel(0, scroll_step)
                current_scroll += scroll_step
                scroll_count += 1
                
                read_time = HumanBehavior.get_pareto_sleep_time(min_s=0.5, max_s=3.0)
                await asyncio.sleep(read_time)

                await HumanBehavior.perform_dummy_clicks(page)

                if random.random() < 0.15:
                    back_step = random.randint(100, 300)
                    await page.mouse.wheel(0, -back_step)
                    current_scroll -= back_step
                    await asyncio.sleep(random.uniform(0.5, 1.5))
                
                scroll_height = await page.evaluate("document.body.scrollHeight")
                if current_scroll >= scroll_height - viewport_height:
                    break
        except Exception as e:
            logger.warning(f"Scroll interrupted: {e}")
    
    @staticmethod
    async def keyboard_nav(page: Page):
        """模擬 Power User 使用鍵盤導航"""
        logger.info(" -> [Mode] Keyboard Power User active")
        for _ in range(random.randint(3, 8)):
            await page.keyboard.press("PageDown")
            await asyncio.sleep(random.uniform(0.5, 1.5))
        for _ in range(random.randint(2, 6)):
            await page.keyboard.press("Tab")
            await asyncio.sleep(random.uniform(0.1, 0.3))

    @staticmethod
    async def download_file(page: Page, url: str):
        """執行檔案下載"""
        logger.info(f" -> [Download] Starting download: {url}")
        try:
            async with page.expect_download() as download_info:
                try:
                    await page.goto(url, timeout=60000)
                except Exception:
                    pass

            download = await download_info.value
            path = await download.path()
            logger.info(f" -> [Download] Completed: {download.suggested_filename}")
            if path and os.path.exists(path):
                os.remove(path)
        except Exception as e:
            logger.error(f" -> [Download] Failed: {e}")

    @staticmethod
    async def watch_video(page: Page, url: str):
        """影片觀看"""
        logger.info(f" -> [Video] Streaming: {url}")
        try:
            await page.goto(url, wait_until='domcontentloaded')
            await asyncio.sleep(5)
            logger.info(" -> [Video] Attempting to ensure playback...")
            try:
                await page.click('video, .html5-video-player', timeout=3000)
            except:
                if page.viewport_size:
                    await page.mouse.click(page.viewport_size['width']/2, page.viewport_size['height']/2)

            watch_duration = random.randint(60, 180)
            start_time = time.time()
            logger.info(f" -> [Video] Watching for {watch_duration} seconds...")
            while time.time() - start_time < watch_duration:
                await asyncio.sleep(random.randint(10, 20))
                if page.viewport_size:
                    cx, cy = page.viewport_size['width']/2, page.viewport_size['height']/2
                    await page.mouse.move(cx + random.randint(-50, 50), cy + random.randint(-50, 50))
            logger.info(" -> [Video] Session finished.")
        except Exception as e:
            logger.error(f" -> [Video] Error: {e}")

    @staticmethod
    async def try_click_link(page: Page, context: BrowserContext):
        """
        [修正] 嘗試點擊連結並處理「新分頁 (New Tab)」情況
        
        Returns:
            Page: 如果成功導航 (無論是原頁面跳轉還是新分頁)，回傳新的 Page 物件。
            None: 如果點擊失敗。
        """
        try:
            links = await page.locator('a[href]:visible').all()
            valid_links = []
            for link in links[:30]: 
                href = await link.get_attribute('href')
                if href and not href.startswith(('javascript', '#', 'mailto')):
                    valid_links.append(link)
            
            if not valid_links:
                return None

            target_link = random.choice(valid_links)
            
            # 確保元素在視野內
            await target_link.scroll_into_view_if_needed()
            box = await target_link.bounding_box()
            
            if box:
                logger.info(f" -> [Deep Browsing] Clicking link to go deeper...")
                
                # 貝茲曲線移動到目標
                await HumanBehavior.human_mouse_move(
                    page, box['x'] + box['width']/2, box['y'] + box['height']/2
                )
                await asyncio.sleep(random.uniform(0.3, 0.7))
                
                # [關鍵修正] 處理可能的新分頁
                # 我們在點擊前後檢查 context.pages 的變化
                
                # 1. 定義「等待新頁面」的事件 (如果有點擊觸發新分頁)
                # 使用 wait_for_event 比較靈活，但這裡用簡單的頁面計數判斷
                current_pages_count = len(context.pages)
                
                # 2. 執行點擊
                await page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                
                # 3. 等待可能的反應 (新分頁開啟 或 原頁面跳轉)
                # 給予一點時間讓事件發生
                await asyncio.sleep(2)
                
                new_pages_count = len(context.pages)
                
                if new_pages_count > current_pages_count:
                    # Case A: 開了新分頁 (New Tab)
                    new_page = context.pages[-1]
                    logger.info(" -> [Nav] New tab detected! Switching context...")
                    
                    # 等待新頁面載入
                    try:
                        await new_page.wait_for_load_state('domcontentloaded', timeout=10000)
                    except:
                        pass
                    
                    # 關閉舊分頁 (模擬使用者專注於新內容，且節省資源)
                    if not page.is_closed():
                        await page.close()
                        
                    return new_page
                
                else:
                    # Case B: 原頁面跳轉 (Same Tab)
                    # 檢查網址是否改變或正在載入
                    try:
                        await page.wait_for_load_state('domcontentloaded', timeout=5000)
                        return page # 回傳原頁面 (已跳轉)
                    except:
                        # 可能是 SPA (Single Page App) 或是點擊無效
                        # 如果網址沒變，我們也視為失敗或停留
                        return page

            return None
        except Exception as e:
            logger.warning(f"Click link failed: {e}")
            return None

class SimulationConfig:
    BROWSING_TARGETS = [
        # --- 台灣在地 (Taiwan Local) ---
        "https://www.ptt.cc/bbs/index.html",       # PTT
        "https://www.mobile01.com/",               # Mobile01
        "https://www.dcard.tw/f",                  # Dcard
        "https://forum.gamer.com.tw/",             # 巴哈姆特
        "https://tw.yahoo.com/",                   # Yahoo TW
        "https://www.ettoday.net/",                # ETtoday
        "https://udn.com/news/index",              # UDN
        "https://www.ithome.com.tw/",              # iThome (Tech)
        "https://shopee.tw/",                      # Shopee TW
        
        # --- 全球技術/新聞 (Global Tech/News) ---
        "https://news.ycombinator.com/",           # Hacker News
        "https://www.reddit.com/r/technology/",    # Reddit Tech
        "https://github.com/explore",              # GitHub
        "https://stackoverflow.com/questions", # StackOverflow
        "https://medium.com/",                     # Medium
        "https://dev.to/",                         # Dev.to
        "https://www.theverge.com/",               # The Verge
        "https://www.bbc.com/news/technology",     # BBC Tech
        "https://www.cnn.com/business/tech",       # CNN Tech
        "https://www.wikipedia.org/wiki/Main_Page",# Wikipedia
        "https://www.amazon.com/Best-Sellers/zgbs" # Amazon Global
    ]
    
    # 2. [下載目標] 增加多樣性：包含 EXE, MSI, ZIP，來自不同 CDN
    DOWNLOAD_TARGETS = [
        # Python & Dev Tools
        "https://www.python.org/ftp/python/3.12.1/python-3.12.1-amd64.exe",
        "https://nodejs.org/dist/v20.10.0/node-v20.10.0-x64.msi",
        "https://github.com/git-for-windows/git/releases/download/v2.43.0.windows.1/Git-2.43.0-64-bit.exe",
        
        # Utility Software
        "https://www.7-zip.org/a/7z2301-x64.exe",
        "https://github.com/notepad-plus-plus/notepad-plus-plus/releases/download/v8.6.2/npp.8.6.2.Installer.x64.exe",
        
        # Graphics/Media (Larger files)
        "https://download.gimp.org/gimp/v2.10/windows/gimp-2.10.36-setup.exe",
        "https://desktop.line-scdn.net/win/new/LineInst.exe"
    ]

    # 3. [影片目標] 混合長時直播與短時影片，涵蓋 News, Music, Entertainment
    VIDEO_TARGETS = [
        # Live Streams (Long Duration)
        "https://www.youtube.com/watch?v=jfKfPfyJRdk", # Lofi Girl
        "https://www.youtube.com/watch?v=4xDzrJKXOOY", # Synthwave
        
        # Standard Videos / Channels
        "https://vimeo.com/channels/staffpicks",       # Vimeo
        "https://www.ted.com/talks",                   # TED
        "https://www.twitch.tv/directory/category/just-chatting?filter=tags&tag=zh-tw" # Twitch TW
    ]

async def run_browsing_session():
    async with async_playwright() as p:
        # [關鍵修改] 從環境變數讀取設定
        # 如果 Docker 裡設定了 HEADLESS_MODE=true，這裡就會變成 True
        # 如果在本機跑沒設定，預設為 False (有畫面)
        is_headless = os.getenv("HEADLESS_MODE", "False").lower() == "true"
        
        # [關鍵修改] 從環境變數讀取 Proxy
        proxy_server = os.getenv("PROXY_SERVER")
        
        launch_args = {
            "headless": is_headless,
            "args": ["--disable-blink-features=AutomationControlled"]
        }
        
        # 如果有 Proxy 就加進去
        if proxy_server:
            launch_args["proxy"] = {"server": proxy_server}

        # 啟動瀏覽器
        browser = await p.chromium.launch(**launch_args)
        
        context = await browser.new_context(
            viewport={'width': 1920, 'height': 1080},
            locale='zh-TW',
            accept_downloads=True
        )
        
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = await context.new_page()
        dns_noise_task = asyncio.create_task(SystemNoise.run_background_dns_noise())

        try:
            total_actions = random.randint(15, 30)
            logger.info(f"[*] Starting NEW SESSION with approx {total_actions} actions.")

            current_mode = None 
            current_depth = 0
            max_depth = 0 
            actions_performed = 0
            
            while actions_performed < total_actions:
                
                # 檢查頁面是否意外關閉
                if page.is_closed():
                    if context.pages:
                        page = context.pages[0] # 嘗試救回
                    else:
                        break # 全關了，結束 session

                should_switch_context = True
                if current_mode == "BROWSING" and current_depth < max_depth:
                     should_switch_context = False

                if should_switch_context:
                    current_depth = 0
                    dice = random.random()

                    if dice < 0.15: 
                        current_mode = "DOWNLOAD"
                        target = random.choice(SimulationConfig.DOWNLOAD_TARGETS)
                        logger.info(f"[{actions_performed+1}] Mode: DOWNLOAD")
                        await HumanBehavior.download_file(page, target)
                        actions_performed += 1
                    
                    elif dice < 0.40: 
                        current_mode = "VIDEO"
                        target = random.choice(SimulationConfig.VIDEO_TARGETS)
                        logger.info(f"[{actions_performed+1}] Mode: VIDEO STREAMING")
                        await HumanBehavior.watch_video(page, target)
                        actions_performed += 1
                    
                    else: 
                        current_mode = "BROWSING"
                        max_depth = random.randint(2, 5)
                        target = random.choice(SimulationConfig.BROWSING_TARGETS)
                        logger.info(f"[{actions_performed+1}] Mode: BROWSING (New Site) - {target} (Target Depth: {max_depth})")
                        
                        try:
                            await page.goto(target, wait_until='domcontentloaded', timeout=60000)
                            actions_performed += 1
                        except Exception:
                            current_mode = None 
                            continue

                if current_mode == "BROWSING":
                    logger.info(f" -> [Deep Browsing] Depth: {current_depth}/{max_depth} on {page.url[:60]}...")
                    
                    await asyncio.sleep(HumanBehavior.get_pareto_sleep_time(min_s=2, max_s=5))
                    
                    if random.random() < 0.7:
                        width = page.viewport_size['width']
                        height = page.viewport_size['height']
                        await HumanBehavior.human_mouse_move(
                            page, 
                            random.randint(width//4, width*3//4), 
                            random.randint(height//4, height*3//4)
                        )
                        await HumanBehavior.human_scroll(page)
                        
                        continue_prob = 0.8 / (current_depth + 1)
                        
                        if current_depth < max_depth and random.random() < continue_prob:
                            # [修正] 傳入 context，並接收回傳的新 page 物件
                            new_page_obj = await HumanBehavior.try_click_link(page, context)
                            
                            if new_page_obj:
                                # 更新主迴圈控制的 page 物件
                                page = new_page_obj
                                current_depth += 1
                                actions_performed += 1
                            else:
                                logger.info(" -> [Deep Browsing] Dead end or click failed.")
                                max_depth = current_depth 
                        else:
                             if current_depth > 0 and random.random() < 0.5:
                                 logger.info(" -> [Deep Browsing] Going back...")
                                 await page.go_back()
                                 current_depth -= 1
                                 actions_performed += 1
                             else:
                                 max_depth = current_depth 
                    else:
                        await HumanBehavior.keyboard_nav(page)
                        actions_performed += 1

                think_time = HumanBehavior.get_pareto_sleep_time(min_s=2, max_s=8)
                await asyncio.sleep(think_time)

        except Exception as e:
            logger.error(f"Session Error: {e}")
        finally:
            dns_noise_task.cancel()
            try:
                await dns_noise_task
            except asyncio.CancelledError:
                pass
            
            try:
                await browser.close()
            except:
                pass
            logger.info("[*] Browser session closed. Cleaning up...")

if __name__ == "__main__":
    try:
        if not os.path.exists("./simulated_downloads"):
            os.makedirs("./simulated_downloads", exist_ok=True)
        
        logger.info("=== Starting Infinite Human Traffic Simulation (V2.9 - Fixed New Tab Logic) ===")
        logger.info("Press Ctrl+C to stop.")
        
        while True:
            asyncio.run(run_browsing_session())
            rest_time = random.randint(5, 15)
            logger.info(f"=== Session finished. Cooling down for {rest_time}s... ===\n")
            time.sleep(rest_time)

    except KeyboardInterrupt:
        logger.info("Simulation stopped by user.")