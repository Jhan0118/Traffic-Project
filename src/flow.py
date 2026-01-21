"""
【Human Traffic Simulator V3.0 - JSON Config & Persona Mode】
"""

import asyncio
import random
import os
import time
import logging
import socket
import json
import numpy as np
from playwright.async_api import async_playwright, Page, BrowserContext

# --- 設定日誌 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("HumanSim")

# --- 預設資料 (Fallback) ---
# 當讀取不到 JSON 時使用的備用資料
DEFAULT_SITES = {
    "local": ["https://www.ptt.cc/bbs/index.html", "https://tw.yahoo.com/"],
    "global_giants": ["https://www.google.com", "https://www.wikipedia.org"],
    "tech": ["https://github.com", "https://stackoverflow.com"],
    "news": ["https://www.bbc.com"],
    "download": ["https://www.python.org/ftp/python/3.12.1/python-3.12.1-amd64.exe"],
    "video": ["https://www.youtube.com/watch?v=jfKfPfyJRdk"]
}

class SystemNoise:
    """系統背景雜訊產生器"""
    NOISE_DOMAINS = [
        "time.windows.com", "time.google.com", "pool.ntp.org",
        "ctldl.windowsupdate.com", "dns.msftncsi.com"
    ]

    @staticmethod
    async def run_background_dns_noise():
        """在背景持續運作，隨機發送 DNS 查詢"""
        while True:
            try:
                target = random.choice(SystemNoise.NOISE_DOMAINS)
                await asyncio.sleep(random.randint(10, 60))
                # logger.info(f" [System Noise] Resolving DNS: {target}") # 減少 Log 雜訊
                try:
                    await asyncio.to_thread(socket.gethostbyname, target)
                except socket.gaierror:
                    pass
            except asyncio.CancelledError:
                break 
            except Exception:
                pass

class ConfigLoader:
    """負責讀取外部 JSON 設定檔"""
    # 注意：這個路徑是對應 Docker 容器內部的掛載路徑
    CONFIG_PATH = "/traffic_data/sites.json"

    @staticmethod
    def load_sites():
        if os.path.exists(ConfigLoader.CONFIG_PATH):
            try:
                with open(ConfigLoader.CONFIG_PATH, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"[*] Successfully loaded sites.json from {ConfigLoader.CONFIG_PATH}")
                    return data
            except Exception as e:
                logger.error(f"[*] Error loading JSON: {e}. Using Default.")
        else:
            logger.warning(f"[*] sites.json not found at {ConfigLoader.CONFIG_PATH}. Using Default.")
        
        return DEFAULT_SITES

class HumanBehavior:
    """人類行為模型 (維持不變，僅保留關鍵邏輯)"""
    _last_mouse_pos = {'x': 0, 'y': 0}

    @staticmethod
    def get_pareto_sleep_time(min_s=2.0, max_s=300.0, alpha=3.0):
        s = (np.random.pareto(alpha) + 1) * min_s
        return min(s, max_s)

    @staticmethod
    def bezier_curve(p0, p1, p2, p3, steps=30):
        path = []
        for t in np.linspace(0, 1, steps):
            x = (1-t)**3 * p0[0] + 3 * (1-t)**2 * t * p1[0] + 3 * (1-t) * t**2 * p2[0] + t**3 * p3[0]
            y = (1-t)**3 * p0[1] + 3 * (1-t)**2 * t * p1[1] + 3 * (1-t) * t**2 * p2[1] + t**3 * p3[1]
            path.append((x, y))
        return path

    @staticmethod
    async def human_mouse_move(page: Page, target_x: float, target_y: float):
        start_box = page.viewport_size
        if not start_box: return
        
        if HumanBehavior._last_mouse_pos['x'] == 0:
            HumanBehavior._last_mouse_pos['x'] = random.randint(0, start_box['width'])
            HumanBehavior._last_mouse_pos['y'] = random.randint(0, start_box['height'])
            await page.mouse.move(HumanBehavior._last_mouse_pos['x'], HumanBehavior._last_mouse_pos['y'])

        start_x, start_y = HumanBehavior._last_mouse_pos['x'], HumanBehavior._last_mouse_pos['y']
        offset = random.randint(100, 500)
        p1 = (start_x + random.randint(-offset, offset), start_y + random.randint(-offset, offset))
        p2 = (target_x + random.randint(-offset, offset), target_y + random.randint(-offset, offset))
        
        path = HumanBehavior.bezier_curve((start_x, start_y), p1, p2, (target_x, target_y), random.randint(20, 50))
        for point in path:
            await page.mouse.move(point[0], point[1])
            HumanBehavior._last_mouse_pos['x'], HumanBehavior._last_mouse_pos['y'] = point[0], point[1]
            await asyncio.sleep(random.uniform(0.001, 0.01))

    @staticmethod
    async def human_scroll(page: Page):
        try:
            scroll_height = await page.evaluate("document.body.scrollHeight")
            current_scroll = 0
            viewport_height = page.viewport_size['height'] if page.viewport_size else 800
            
            for _ in range(random.randint(3, 10)):
                scroll_step = random.randint(int(viewport_height * 0.3), int(viewport_height * 0.7))
                await page.mouse.wheel(0, scroll_step)
                current_scroll += scroll_step
                await asyncio.sleep(HumanBehavior.get_pareto_sleep_time(min_s=0.5, max_s=3.0))
                
                # 偶爾回滾
                if random.random() < 0.15:
                    await page.mouse.wheel(0, -random.randint(100, 300))
                    await asyncio.sleep(1)

                scroll_height = await page.evaluate("document.body.scrollHeight")
                if current_scroll >= scroll_height - viewport_height: break
        except: pass

    @staticmethod
    async def try_click_link(page: Page, context: BrowserContext):
        try:
            links = await page.locator('a[href]:visible').all()
            valid_links = [l for l in links[:30] if await l.get_attribute('href') and not (await l.get_attribute('href')).startswith(('javascript', '#'))]
            
            if not valid_links: return None
            target_link = random.choice(valid_links)
            await target_link.scroll_into_view_if_needed()
            box = await target_link.bounding_box()
            
            if box:
                logger.info(" -> [Deep Browsing] Clicking link...")
                await HumanBehavior.human_mouse_move(page, box['x']+box['width']/2, box['y']+box['height']/2)
                await asyncio.sleep(random.uniform(0.3, 0.7))
                
                current_count = len(context.pages)
                await page.mouse.click(box['x']+box['width']/2, box['y']+box['height']/2)
                await asyncio.sleep(2)
                
                if len(context.pages) > current_count:
                    new_page = context.pages[-1]
                    logger.info(" -> [Nav] New tab detected! Switching...")
                    try: await new_page.wait_for_load_state('domcontentloaded', timeout=10000)
                    except: pass
                    if not page.is_closed(): await page.close()
                    return new_page
                else:
                    try: await page.wait_for_load_state('domcontentloaded', timeout=5000)
                    except: pass
                    return page
            return None
        except: return None

    @staticmethod
    async def download_file(page: Page, url: str):
        logger.info(f" -> [Download] Start: {url}")
        try:
            async with page.expect_download() as download_info:
                try: await page.goto(url, timeout=60000)
                except: pass
            
            download = await download_info.value
            path = await download.path()
            logger.info(f" -> [Download] Done: {download.suggested_filename}")
            if path and os.path.exists(path): os.remove(path)
        except Exception as e:
            logger.error(f" -> [Download] Failed: {e}")

    @staticmethod
    async def watch_video(page: Page, url: str):
        logger.info(f" -> [Video] Streaming: {url}")
        try:
            await page.goto(url, wait_until='domcontentloaded')
            await asyncio.sleep(5)
            # 嘗試點擊播放
            try: await page.click('video, .html5-video-player', timeout=3000)
            except: pass
            
            watch_duration = random.randint(180, 1800)
            logger.info(f" -> [Video] Watching for {watch_duration}s...")
            await asyncio.sleep(watch_duration)
        except: pass

async def run_browsing_session(sites_config):
    async with async_playwright() as p:
        is_headless = os.getenv("HEADLESS_MODE", "False").lower() == "true"
        launch_args = {"headless": is_headless, "args": ["--disable-blink-features=AutomationControlled"]}
        
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(viewport={'width': 1920, 'height': 1080}, locale='zh-TW', accept_downloads=True)
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        page = await context.new_page()
        
        dns_task = asyncio.create_task(SystemNoise.run_background_dns_noise())

        try:
            # --- Persona (興趣) 隨機選擇 ---
            # 每次 Session 隨機扮演一種角色，決定它會去逛哪些網站
            persona = random.choice(['TECH_GEEK', 'NEWS_ADDICT', 'LOCAL_USER', 'MIXED'])
            
            session_targets = []
            if persona == 'TECH_GEEK':
                session_targets = sites_config.get('tech', []) + sites_config.get('global_giants', [])
            elif persona == 'NEWS_ADDICT':
                session_targets = sites_config.get('news', []) + sites_config.get('local', [])
            elif persona == 'LOCAL_USER':
                session_targets = sites_config.get('local', []) + sites_config.get('global_giants', [])
            else:
                # MIXED: 混合所有 browsing 類型的網址
                for key in ['local', 'global_giants', 'tech', 'news']:
                    session_targets.extend(sites_config.get(key, []))
            
            # 確保有網址可以逛，避免空清單
            if not session_targets: session_targets = DEFAULT_SITES['global_giants']

            total_actions = random.randint(10, 25)
            logger.info(f"[*] NEW SESSION | Persona: {persona} | Actions: {total_actions}")

            actions = 0
            while actions < total_actions:
                if page.is_closed(): 
                    if context.pages: page = context.pages[0]
                    else: break

                dice = random.random()
                
                # 10% 機率下載
                if dice < 0.10:
                    dl_list = sites_config.get('download', [])
                    if dl_list:
                        await HumanBehavior.download_file(page, random.choice(dl_list))
                        actions += 1
                
                # 20% 機率看影片
                elif dice < 0.30:
                    vid_list = sites_config.get('video', [])
                    if vid_list:
                        await HumanBehavior.watch_video(page, random.choice(vid_list))
                        actions += 1
                
                # 70% 機率一般瀏覽
                else:
                    target = random.choice(session_targets)
                    max_depth = random.randint(2, 4)
                    logger.info(f"[{actions+1}] Browsing: {target} (Depth: {max_depth})")
                    try:
                        await page.goto(target, wait_until='domcontentloaded', timeout=60000)
                        actions += 1
                        
                        # 深度瀏覽邏輯
                        current_depth = 0
                        while current_depth < max_depth:
                            await asyncio.sleep(random.uniform(2, 5))
                            
                            # 隨機滑動
                            if random.random() < 0.7:
                                await HumanBehavior.human_scroll(page)
                            
                            # 點擊連結深入
                            if random.random() < 0.6:
                                new_page = await HumanBehavior.try_click_link(page, context)
                                if new_page:
                                    page = new_page
                                    current_depth += 1
                                    actions += 1
                                else:
                                    break
                            else:
                                # 隨機回上一頁
                                if current_depth > 0 and random.random() < 0.3:
                                    await page.go_back()
                                    current_depth -= 1
                            
                            if page.is_closed(): break

                    except Exception: pass
                
                await asyncio.sleep(random.randint(5, 10))

        finally:
            dns_task.cancel()
            await browser.close()

if __name__ == "__main__":
    logger.info("=== Starting V3.0 Simulation (Configurable) ===")
    
    # 在主程式啟動時載入一次 Config
    # 實際運作時，如果要熱更新 Config，可以在 while 迴圈內重新 load
    current_config = ConfigLoader.load_sites()
    
    while True:
        # 這裡每次迴圈都重新載入，實現真正的「熱更新」
        # 只要你修改了 JSON，下一個 Session 就會生效
        current_config = ConfigLoader.load_sites()
        
        asyncio.run(run_browsing_session(current_config))
        time.sleep(random.randint(5, 15))