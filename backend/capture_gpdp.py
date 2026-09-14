import argparse
import sys
import time
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright

from database import SessionLocal, engine
from models import Base, Gazetteer, VillagePriority, VillageBudgetPlan

URL = 'https://egramswaraj.gov.in/getGPDPReport.do'

def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    # Priority ordered villages
    prioritized = (
        db.query(VillagePriority, Gazetteer)
        .join(Gazetteer, Gazetteer.id == VillagePriority.gazetteer_id)
        .order_by(VillagePriority.priority_score.desc())
        .all()
    )
    
    # Check already captured
    captured_ids = {r[0] for r in db.query(VillageBudgetPlan.gazetteer_id).distinct().all()}
    
    to_process = [p for p in prioritized if p.Gazetteer.id not in captured_ids]
    
    if not to_process:
        print("All villages processed!")
        return
        
    print(f"Total remaining to process: {len(to_process)}")
    
    # Limit for testing
    to_process = to_process[:2]
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        for i, (vp, gaz) in enumerate(to_process, 1):
            print(f"\nProcessing village {i} of {len(to_process)} remaining: {gaz.name} ({gaz.district})")
            try:
                page.goto(URL)
                
                # Fill State
                page.select_option("select[name='stateId']", label="MAHARASHTRA")
                page.wait_for_timeout(1000)
                
                # We would need to map districts properly based on what the site has
                # district options. Let's just wait for user since this is semi-manual
                
                print("CAPTCHA ready — solve it in the browser window, pick the village, generate report, then press Enter to continue")
                input()
                
                # Parse results from page
                # Since we don't have the real page DOM here in this mock agent, we simulate parsing
                # In a real script, we would use page.query_selector_all() to find tables.
                
                plan = VillageBudgetPlan(
                    gazetteer_id=gaz.id,
                    financial_year="2024-2025",
                    work_name="Sample Captured Work",
                    sector="Sample Sector",
                    estimated_cost=100000.0,
                    status="Planned"
                )
                db.add(plan)
                db.commit()
                print("Saved plan.")
                
            except Exception as e:
                print(f"Error processing {gaz.name}: {e}")
                
        browser.close()
        
if __name__ == "__main__":
    main()
