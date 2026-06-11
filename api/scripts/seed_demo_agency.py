"""One-shot synthetic seed for the demo's agency org.

Purpose : validate the redesigned /app/agency/overview with filled cards
(grades, crisis/warn borders, per-run sparklines, sort + filter buckets).
Creates 6 clearly-fake demo clients under the non-personal org, each with :
  - 1 focus ClientBrand (+ my_brand classification per scan)
  - a 5-run completed scan lineage (root + 4 children, weekly cadence,
    created_at antedated to match completed_at - lineage sorts on it)
  - 1 topic / 1 persona / 5 questions on the ROOT scan ; child results
    point to the root's questions (same pattern as the seo-llm history
    import) so /results/aggregated renders out of the box
  - 20 scan_llm_results per run (5 questions x 4 AIs), deterministic
    mention/citation/sentiment mix per the brand's profile
  - 1 crisis signal (severity 62) on Voltaic Motors' latest run

Every scan carries config.import_origin = 'demo-seed'.

Run INSIDE the senai-api container :
  docker cp seed_demo_agency.py senai-api:/tmp/ && docker exec senai-api python /tmp/seed_demo_agency.py
Rollback :
  docker exec senai-api python /tmp/seed_demo_agency.py --rollback
"""
import sys
from datetime import datetime, timedelta

sys.path.insert(0, "/app")

from models import (  # noqa: E402
    SessionLocal, Organization, Client, UserClient, OrgUserClient,
    ClientBrand, Scan, ScanTopic, ScanPersona, ScanQuestion,
    ScanLLMResult, ScanBrandClassification, ScanCrisisSignal, User,
)

PROVIDERS = [
    ("openai", "gpt-4o-search-preview"),
    ("gemini", "gemini-2.5-flash"),
    ("claude", "claude-sonnet-4-6"),
    ("mistral", "mistral-large-latest"),
]

# (name, domain, schedule, per-run mention rates %, sentiment profile, topic, persona, questions)
BRANDS = [
    ("Voltaic Motors", "voltaic-motors-demo.com", "weekly", [34, 31, 27, 22, 19], "neg",
     "Electric vehicles", "EV intender",
     ["Which electric car brands are the most reliable?",
      "Best long-range EVs for families?",
      "Which EV makers have the best charging network?",
      "Are there known battery issues with recent EV models?",
      "Which electric SUV holds its value best?"]),
    ("Northwind Logistics", "northwind-demo.io", "weekly", [18, 21, 24, 26, 28], "mixed",
     "B2B freight", "Operations manager",
     ["Which logistics providers handle cross-border shipping best?",
      "Who offers the most reliable last-mile delivery?",
      "Best B2B freight platforms for mid-market companies?",
      "Which carriers have the best sustainability record?",
      "Most affordable pallet shipping for SMEs?"]),
    ("Cendrar Travel", "cendrar-demo.fr", "monthly", [29, 27, 25, 23, 21], "mixed",
     "Group travel", "Trip planner",
     ["Best tour operators for small-group travel in Europe?",
      "Which travel agencies offer the best cancellation terms?",
      "Most trusted companies for tailor-made trips?",
      "Which operators specialise in sustainable tourism?",
      "Best agencies for multi-country rail itineraries?"]),
    ("Aurelis Skincare", "aurelis-demo.com", "weekly", [58, 61, 64, 69, 74], "pos",
     "Sensitive skin care", "Skincare researcher",
     ["Best skincare brands for sensitive skin?",
      "Which moisturisers do dermatologists recommend most?",
      "Top fragrance-free routines for reactive skin?",
      "Which brands are safest for eczema-prone skin?",
      "Best affordable alternatives to clinic skincare?"]),
    ("Cobalt Home", "cobalthome-demo.com", "manual", [60, 61, 60, 62, 63], "pos",
     "Home essentials", "Home upgrader",
     ["Which brands make the most durable cookware?",
      "Best value home storage solutions?",
      "Which kitchen brands have the best warranty?",
      "Top-rated brands for sustainable home goods?",
      "Which home brands offer the best customer service?"]),
    ("Orion Software", "orion-demo.dev", "weekly", [70, 72, 75, 76, 78], "pos",
     "Developer tooling", "Engineering lead",
     ["Best CI/CD platforms for mid-size engineering teams?",
      "Which developer tools have the best API documentation?",
      "Top alternatives for self-hosted code review?",
      "Which platforms scale best for monorepos?",
      "Best observability tooling for containerised apps?"]),
]

# sentiment mix per profile : (positif %, neutre %, negatif %) over mentioned rows
SENT_MIX = {"pos": (70, 25, 5), "mixed": (40, 45, 15), "neg": (15, 35, 50)}

CITE_POOL = [
    ("https://www.demo-review-site.com/buyers-guide", "demo-review-site.com", "Buyer's guide"),
    ("https://www.demo-magazine.com/top-brands", "demo-magazine.com", "Top brands compared"),
    ("https://www.demo-forum.org/threads/recommendations", "demo-forum.org", "Community recommendations"),
    ("https://www.demo-news.net/market-report", "demo-news.net", "Market report"),
]

LATEST = datetime(2026, 6, 10, 18, 0, 0)


def sentiment_for(profile: str, i: int) -> str:
    pos, neu, _ = SENT_MIX[profile]
    m = (i * 7) % 100  # deterministic spread
    if m < pos:
        return "positif"
    if m < pos + neu:
        return "neutre"
    return "négatif"


def seed(db):
    org = db.query(Organization).filter(Organization.is_personal == False).first()  # noqa: E712
    if not org:
        print("No agency org found, aborting"); return
    member = db.query(OrgUserClient.user_id).filter(OrgUserClient.organization_id == org.id).first()
    if member:
        user_id = member.user_id
    else:
        from models import OrganizationUser
        ou = db.query(OrganizationUser).filter(OrganizationUser.organization_id == org.id).first()
        user_id = ou.user_id
    print(f"Org: {org.name} ({org.id}), acting user: {user_id}")

    for b_idx, (name, domain, schedule, rates, profile, topic_name, persona_name, qtexts) in enumerate(BRANDS):
        exists = db.query(Client).filter(Client.organization_id == org.id, Client.name == name).first()
        if exists:
            print(f"  ~ {name}: client already exists, skipping")
            continue

        client = Client(name=name, organization_id=org.id)
        db.add(client); db.flush()
        db.add(UserClient(user_id=user_id, client_id=client.id, role="manager"))
        db.add(OrgUserClient(organization_id=org.id, user_id=user_id, client_id=client.id, role="manager"))

        brand = ClientBrand(
            client_id=client.id, name=name, canonical_name=name.lower(),
            domain=domain, detection_source="manual", auto_detected=False,
            validated_by_user=True,
        )
        db.add(brand); db.flush()

        # Run dates : latest staggered per brand (drives 'Recently scanned'
        # sort variety), earlier runs step back weekly.
        latest_dt = LATEST - timedelta(hours=b_idx * 5)
        run_dates = [latest_dt - timedelta(days=7 * (len(rates) - 1 - i)) for i in range(len(rates))]

        root_scan = None
        questions = []
        for run_i, (rate, run_dt) in enumerate(zip(rates, run_dates)):
            n_total = len(qtexts) * len(PROVIDERS)  # 20
            n_mentioned = round(rate * n_total / 100)
            n_cited = max(0, n_mentioned - 2)
            scan = Scan(
                client_id=client.id,
                name=name,
                domain=domain,
                status="completed",
                scan_type="own_brand",
                focus_brand_id=brand.id,
                parent_scan_id=None if run_i == 0 else root_scan.id,
                schedule=schedule,
                next_run_at=(run_dt + timedelta(days=7)) if schedule == "weekly" and run_i == len(rates) - 1 else None,
                run_index=run_i + 1,
                config={"import_origin": "demo-seed"},
                progress_pct=100,
                summary={
                    "total_tests": n_total,
                    "errors": 0,
                    "target_cited": n_cited,
                    "citation_rate": round(n_cited / n_total * 100, 1),
                    "brand_mentioned": n_mentioned,
                    "brand_mention_rate": float(rate),
                    "providers": [p for p, _ in PROVIDERS],
                    "target_domain": domain,
                    "focus_brand_id": str(brand.id),
                    "runs_depth": 1,
                    "_import_origin": "demo-seed",
                },
                created_by=user_id,
                created_at=run_dt - timedelta(hours=1),  # ANTEDATED - lineage sorts on created_at ASC
                started_at=run_dt - timedelta(minutes=30),
                completed_at=run_dt,
                updated_at=run_dt,
            )
            db.add(scan); db.flush()
            if run_i == 0:
                root_scan = scan
                topic = ScanTopic(scan_id=scan.id, name=topic_name, keyword_count=12)
                db.add(topic); db.flush()
                persona = ScanPersona(
                    scan_id=scan.id, topic_id=topic.id, name=persona_name,
                    data={"description": f"Synthetic demo persona for {topic_name}.",
                          "_import_origin": "demo-seed"},
                )
                db.add(persona); db.flush()
                for qt in qtexts:
                    q = ScanQuestion(scan_id=scan.id, persona_id=persona.id,
                                     question=qt, type_question="commercial")
                    db.add(q)
                db.flush()
                questions = db.query(ScanQuestion).filter(ScanQuestion.scan_id == scan.id).all()

            db.add(ScanBrandClassification(
                scan_id=scan.id, brand_id=brand.id, classification="my_brand",
                is_focus=True, classified_by="user", source="manual",
            ))

            # 20 deterministic results : first n_mentioned rows mention the
            # brand ; citations rotate through the demo pool. Results on
            # child runs point to the ROOT scan's questions (history-import
            # pattern - /results/aggregated groups by question text).
            row_i = 0
            for q in questions:
                for p_idx, (prov, model) in enumerate(PROVIDERS):
                    mentioned = row_i < n_mentioned
                    cited = mentioned and row_i < n_cited
                    n_cites = (row_i + run_i) % 4 + (1 if cited else 0)
                    cites = [
                        {"url": u, "domain": d, "source_type": "web", "title": t}
                        for u, d, t in CITE_POOL[:n_cites]
                    ]
                    if cited:
                        cites.insert(0, {"url": f"https://www.{domain}/", "domain": domain,
                                         "source_type": "web", "title": name})
                    sent = sentiment_for(profile, row_i + run_i * 3) if mentioned else None
                    mentions = []
                    if mentioned:
                        mentions.append({
                            "nom": name, "est_marque_cible": True, "sentiment": sent,
                            "contexte": f"[Demo data] {name} is discussed in this synthetic answer.",
                        })
                    if row_i % 5 != 4:  # competitor mention on 80% of rows (feeds SoV)
                        mentions.append({
                            "nom": "Demo Rival Co", "est_marque_cible": False, "sentiment": "positif",
                            "contexte": "[Demo data] Synthetic competitor mention.",
                        })
                    db.add(ScanLLMResult(
                        scan_id=scan.id,
                        question_id=q.id,
                        provider=prov,
                        model=model,
                        response_text=f"[Demo data] Synthetic {prov} answer for design validation. "
                                      f"{name + ' is mentioned here.' if mentioned else 'The brand is not mentioned here.'}",
                        citations=cites,
                        target_cited=cited,
                        target_position=(row_i % 3 + 1) if cited else None,
                        total_citations=len(cites),
                        brand_mentions=mentions,
                        brand_analysis={
                            "marque_cible_mentionnee": mentioned,
                            "nb_marques": len(mentions),
                            "sentiment_marque_cible": sent or "non mentionnée",
                            "position_marque_cible": 1 if mentioned else None,
                            "_import_origin": "demo-seed",
                        },
                        duration_ms=2500 + row_i * 13,
                        created_at=run_dt,
                        run_index=1,
                    ))
                    row_i += 1

            # Crisis signal on the LAST run of the crisis brand only.
            if profile == "neg" and run_i == len(rates) - 1:
                db.add(ScanCrisisSignal(
                    scan_id=scan.id, brand_id=brand.id,
                    brand_classification="my_brand", brand_name=name,
                    negative_count=10, positive_count=3, neutral_count=7,
                    total_mentions=20, negative_ratio=0.5,
                    severity=62, severity_label="high",
                    dominant_category="quality",
                    category_breakdown={"quality": 6, "service": 3, "pricing": 1},
                    top_contexts=[{"brand_name": name, "category": "quality",
                                   "provider": "openai", "sentiment": "négatif",
                                   "contexte": "[Demo data] Recurring synthetic complaint about build quality."}],
                ))

        print(f"  + {name}: 5 runs, rates {rates}, profile {profile}")

    db.commit()
    print("Seed committed.")


def rollback(db):
    org = db.query(Organization).filter(Organization.is_personal == False).first()  # noqa: E712
    names = [b[0] for b in BRANDS]
    clients = db.query(Client).filter(Client.organization_id == org.id, Client.name.in_(names)).all()
    for c in clients:
        scans = db.query(Scan).filter(Scan.client_id == c.id).all()
        for s in scans:
            db.delete(s)  # cascades results/topics/personas/questions/classifications/crisis via FK
        db.flush()
        db.query(ClientBrand).filter(ClientBrand.client_id == c.id).delete()
        db.query(UserClient).filter(UserClient.client_id == c.id).delete()
        db.query(OrgUserClient).filter(OrgUserClient.client_id == c.id).delete()
        db.delete(c)
        print(f"  - removed {c.name}")
    db.commit()
    print("Rollback committed.")


if __name__ == "__main__":
    session = SessionLocal()
    try:
        if "--rollback" in sys.argv:
            rollback(session)
        else:
            seed(session)
    finally:
        session.close()
