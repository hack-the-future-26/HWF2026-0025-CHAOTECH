import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
import dateutil.parser
from database import SessionLocal, engine
from models import Base, HazardAlert

RSS_URL = "https://sachet.ndma.gov.in/cap_public_website/rss/rss_maharashtra.xml"
NS = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    try:
        res = requests.get(RSS_URL, timeout=30)
        root = ET.fromstring(res.text)
        
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []
        
        for item in items:
            link = item.find("link")
            if link is None or not link.text:
                continue
            
            identifier = link.text.split("identifier=")[-1]
            
            # Check if exists
            if db.query(HazardAlert).filter_by(identifier=identifier).first():
                continue
                
            try:
                cap_res = requests.get(link.text, timeout=30)
                if "No Active Alert" in cap_res.text:
                    continue
                
                cap_root = ET.fromstring(cap_res.text)
                
                info = cap_root.find("cap:info", NS)
                if info is None:
                    continue
                    
                event = info.find("cap:event", NS)
                severity = info.find("cap:severity", NS)
                urgency = info.find("cap:urgency", NS)
                effective = info.find("cap:effective", NS)
                expires = info.find("cap:expires", NS)
                sender = info.find("cap:senderName", NS)
                
                event_text = event.text if event is not None else None
                sev_text = severity.text if severity is not None else None
                urg_text = urgency.text if urgency is not None else None
                eff_text = effective.text if effective is not None else None
                exp_text = expires.text if expires is not None else None
                snd_text = sender.text if sender is not None else None
                
                eff_dt = dateutil.parser.parse(eff_text) if eff_text else None
                exp_dt = dateutil.parser.parse(exp_text) if exp_text else None
                
                areas = info.findall("cap:area", NS)
                districts_found = set()
                area_descs = []
                
                for area in areas:
                    ad = area.find("cap:areaDesc", NS)
                    if ad is not None and ad.text:
                        area_descs.append(ad.text)
                        txt = ad.text.lower()
                        if "nashik" in txt or "नाशिक" in txt:
                            districts_found.add("Nashik")
                        if "kolhapur" in txt or "कोल्हापूर" in txt:
                            districts_found.add("Kolhapur")
                            
                districts_str = ",".join(districts_found) if districts_found else None
                
                alert = HazardAlert(
                    identifier=identifier,
                    event=event_text,
                    severity=sev_text,
                    urgency=urg_text,
                    area_desc="; ".join(area_descs),
                    districts=districts_str,
                    effective=eff_dt,
                    expires=exp_dt,
                    sender=snd_text
                )
                db.add(alert)
                
            except Exception as e:
                print(f"Error processing {identifier}: {e}")
                
        db.commit()
    finally:
        db.close()

if __name__ == "__main__":
    main()
