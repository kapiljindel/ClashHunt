import urllib.parse
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

def scrape_fwa_details(clan_tag):
    """
    SECONDARY UTILITY FUNCTION: 
    Scrapes FWA details for a specific clan tag and returns data to the main bot.
    """
    clean_tag = clan_tag.replace("#", "").strip()
    
    # Default fallback data if the site fails or times out
    data = {
        "point_balance": "N/A",
        "war_id": "N/A",
        "sync_num": "N/A",
        "match_type": "Unknown"
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True) 
        page = browser.new_page()
        
        try:
            page.goto(f"https://points.fwafarm.com/clan?tag={clean_tag}", timeout=25000)
            page.wait_for_load_state("networkidle")
            
            body_text = page.locator("body").inner_text()
            clean_lines = [line.strip() for line in body_text.split("\n") if line.strip()]
            
            win_calculator_heading = ""
            match_status = ""
            
            for i, line in enumerate(clean_lines):
                if "Point Balance:" in line:
                    data["point_balance"] = line.split(":", 1)[1].strip()
                    
                if "Win Calculator for" in line:
                    win_calculator_heading = line
                    if i + 2 < len(clean_lines):
                        match_status = clean_lines[i + 2]
            
            # Parse War ID & Sync Number
            if "War #" in win_calculator_heading:
                try:
                    data["war_id"] = win_calculator_heading.split("War #")[1].split(" ")[0].strip()
                except Exception: pass
            if "Sync #" in win_calculator_heading:
                try:
                    data["sync_num"] = win_calculator_heading.split("Sync #")[1].split(" ")[0].strip()
                except Exception: pass

            # Calculate Match Type
            if "Not marked as an FWA match" in match_status:
                data["match_type"] = "Mismatch"
            elif "should win" in match_status or "points" in match_status:
                data["match_type"] = "FWA Match"
            else:
                data["match_type"] = "Verification Required"

        except PlaywrightTimeoutError:
            print(f"[Scraper Warning] Page timed out for tag {clean_tag}. Using fallbacks.")
        except Exception as e:
            print(f"[Scraper Error] Unexpected background issue: {e}")
        finally:
            browser.close()
            
    return data

# Prevent direct execution from the terminal
if __name__ == "__main__":
    print("\n[!] This is a secondary utility module used by the Discord bot.")
    print("[!] To start the bot, run: python main.py\n")