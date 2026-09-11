import pandas as pd
import random
from datetime import datetime, timedelta

def generate_sample_cases(n=100):
    courts = [
        ("txed", "TXED", "Eastern District of Texas"),
        ("ded", "DED", "District of Delaware"),
        ("cand", "NDCA", "Northern District of California"),
        ("cacd", "CACD", "Central District of California"),
        ("txwd", "TXWD", "Western District of Texas"),
        ("nysd", "SDNY", "Southern District of New York"),
        ("ilnd", "ILND", "Northern District of Illinois")
    ]
    
    plaintiffs = [
        "Uniloc USA, Inc.", "WSOU Investments LLC", "VLSI Technology LLC", "Neodron Ltd.",
        "Scramoge Technology Ltd.", "Daedalus Blue LLC", "Solas OLED Ltd.", "Sonos, Inc.",
        "Bell Northern Research LLC", "Data Scramble LLC", "Core Wireless Licensing S.a.r.l.",
        "Intellectual Ventures I LLC", "Optis Cellular Technology LLC", "Maxell, Ltd.",
        "Koninklijke Philips N.V.", "Nokia Technologies Oy", "Telefonaktiebolaget LM Ericsson"
    ]
    
    defendants = [
        "Apple Inc.", "Google LLC", "Samsung Electronics Co., Ltd.", "Amazon.com, Inc.",
        "Microsoft Corporation", "LG Electronics Inc.", "Dell Technologies Inc.",
        "Lenovo Group Ltd.", "Sony Corporation", "Cisco Systems, Inc.", "ASUSTeK Computer Inc.",
        "Motorola Mobility LLC", "T-Mobile USA, Inc.", "AT&T Mobility LLC"
    ]
    
    tech_titles = [
        "Wireless Communication & 5G Beamforming Protocol",
        "Adaptive Power Management in Mobile Handsets",
        "High-Efficiency Video Coding (HEVC / H.265)",
        "Capacitive Touchscreen Gesture Recognition",
        "OLED Display Sub-pixel Rendering Architecture",
        "Cryptographic Key Exchange in Distributed Sensor Networks",
        "Multi-Carrier OFDM Signal Processing in Cellular Radios",
        "Cloud Data Synchronization & Cache Invalidation",
        "Audio Beamforming & Acoustic Echo Cancellation",
        "Memory Controller Prefetch Buffer Optimization"
    ]
    
    data = []
    base_date = datetime(2021, 1, 15)
    
    for i in range(1, n + 1):
        court_id, court_abbr, court_name = random.choice(courts)
        year = random.choice([20, 21, 22, 23])
        docket_num = f"{random.choice([1, 2])}:{year}-cv-{str(random.randint(1, 999)).zfill(5)}"
        filing_date = base_date + timedelta(days=random.randint(0, 1000))
        
        p = random.choice(plaintiffs)
        d = random.choice([x for x in defendants if x != p])
        num_pat = random.choice([1, 2, 3, 4, 5])
        pat_list = [f"US{random.randint(7000000, 11500000)}" for _ in range(num_pat)]
        tech = random.choice(tech_titles)
        
        data.append({
            "CaseNumber": docket_num,
            "Date of Filing": filing_date.strftime("%Y-%m-%d"),
            "Plaintiff": p,
            "Defendents": d,
            "JURISDICTION": court_abbr,
            "No.of Patents": num_pat,
            "Patent Numbers": ", ".join(pat_list),
            "Technology of patents Title": tech
        })
        
    df = pd.DataFrame(data)
    out_path = r"C:\Users\Vaibhav\.gemini\antigravity\scratch\patent-docket-tracker\sample_data\sample_patent_cases.xlsx"
    df.to_excel(out_path, index=False)
    print(f"Generated {len(df)} sample patent cases at {out_path}")

if __name__ == "__main__":
    generate_sample_cases(100)
