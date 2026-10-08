# Manufacturing Operations Center: Demo Talk Track

**Audience:** semiconductor and electronics manufacturing (yield/test engineering, manufacturing IT, data and analytics leaders)
**Length:** about 23 minutes, plus Q&A. There are cut points for a 10-minute version.
**Story in one line:** *A probe-card change quietly cost seven points of yield. Watch how fast we go from "FPY looks off" to "it's PC-AUS-447 on three testers, here's the dollar impact, and here's the wafer-map proof", without leaving one governed platform.*

---

## Pre-demo checklist (15 minutes before)

- [ ] Open the app at https://mfg-ops-center-1444828305810485.aws.databricksapps.com and click through all five tabs.
  - If Genie or Agent returns 403, the OAuth consent has gone stale. See DESIGN.md §8.
- [ ] Warm up Lakebase: open **Wafer Operations** once. The Lakebase compute scales to zero after 5 idle minutes and wakes in well under a second, but the first load after idle is the slowest.
- [ ] Check **Data Pipeline** shows the last run as SUCCESS. Don't start a refresh right before the demo: a full run takes about 25 minutes.
- [ ] Warm up the warehouse: load the dashboard once. `parijat-mfg-ops` auto-stops after 10 minutes, and a cold start is about 5–10 seconds.
- [ ] Warm up the agents: ask the Supervisor Agent one throwaway question. The first call can take 30–60 seconds.
- [ ] Dashboard: clear all filters. Check that the wafer map shows **MX7-F12-W27-W01**.
- [ ] Have a backup tab open on the workspace:
  - Catalog Explorer → `parijat_demos.mfg_ops`, for the lineage and governance segment;
  - the job run page for job `848871296768056`.
- [ ] Optionally, keep `bundle/databricks.yml` open in an editor for the "it's all code" moment.

---

## 0. Opening: set the scene (1 minute)

> "Every fab and OSAT I talk to has the same problem. Test data, STDF files off the testers, is the richest signal in the factory, but it's trapped. It sits in one tool for yield, another for wafer maps, a spreadsheet for the change log and someone's head for root cause. When yield moves, the war room spends days stitching that together.
>
> I'm going to show you that whole loop on Databricks: ingest, yield analytics, wafer-map pattern recognition, root cause and dollar impact, with AI on top, all on one governed copy of the data."

**Setup facts to drop:**
- KARI Semiconductor Ops (fictional), with three sites: Austin (AUS), Hsinchu (HSC) and Penang (PNG).
- Products MX-5, MX-7 and RF-22, from foundries F10 and F12.
- About 4 months of wafer sort: 1,200 wafers and about 566K die results.

---

## 1. The Operations Center app (1 minute)

**Show:** the app landing page and its five tabs.

> "This is a Databricks App, a full web application running inside the platform, secured by the same identity. There's no extract and no separate BI server. It has five views: an operational dashboard, a wafer operations desk running on Lakebase, Genie for ad-hoc questions, an AI supervisor agent, and the data pipeline that keeps all of it fresh."

---

## 2. Symptom: the dashboard (4 minutes)

**Click:** the Dashboard tab, page "Semiconductor Test Quality and Throughput".

1. **FPY by Date:**
   > "Here's first-pass yield by site. HSC and PNG are flat. Austin drops off a cliff on **August 18th**. MX-7 at Austin goes from about **95.4% to 88.3%**, with a daily low of **86.5% on the 19th**. That's roughly seven points."
   - *Optional:* set the global filter **Product = MX-7** to isolate it.
2. **Failure Bin Mix:**
   > "And it's not random. One bin explodes: **HB_021, open/short**, a contact failure. Its share of dies goes from about 1.5% to almost 9%."
3. **Top Drifting Parameters:**
   > "The parametric data agrees. **PT_0210, contact resistance**, shifts several sigma above its pre-event baseline at Austin. Electrically, something is wrong with how we're touching the wafer."
4. **Cumulative Die Loss and Cost:**
   > "Here's what it costs. Against the 21-day baseline, Austin lost about **1,100 extra dies, roughly $17.6K, in ten days**, just on MX-7. Then the curve goes flat: something fixed it."
5. **Equipment and Recipe Changes:**
   > "The change log lines up: **Aug 18, 07:30**, probe card **PC-AUS-447 Rev C** plus handler firmware **HF-3.2.1** on testers **TST-AUS-03, 04 and 05**. Then **Aug 24, 22:15**, a firmware rollback and probe card recondition. That's when the curve flattens."

**Transition:**
> "That's correlation. A yield engineer wants *physical* evidence. Let's look at the wafers."

---

## 3. Signature: wafer-map pattern recognition (4 minutes)

**Click:** the **Wafer Map Patterns** page.

1. **Header counters:**
   > "Every wafer is probed in one sort session, so we have a complete die map for all 1,200 wafers. The pipeline classifies each map into the industry-standard **WM-811K** pattern classes: Center, Donut, Edge-Ring, Edge-Loc, Loc, Scratch, Random, None."
2. **Weekly Wafer Pattern Mix:**
   > "Normally you see a background of a few patterned wafers a week: a scratch here, a center hot spot there. The week of **August 18th, Edge-Ring spikes**."
3. **Patterned Wafers by Tester:**
   > "And the Edge-Ring wafers aren't spread across the floor. They're on **TST-AUS-05, 03 and 04**, the three testers that got the new probe card. Every one of the 30 incident Edge-Ring wafers ran on **PC-AUS-447**."
4. **Wafer Map** (defaults to `MX7-F12-W27-W01`):
   > "Here's one of them: wafer 1 of lot MX7-F12-W27, probed on TST-AUS-05 on the 18th, at 83.6% yield. You can see the ring. Almost **40% of edge dies fail**, against about 10% in the center, and most edge failures are HB_021. That's a classic probe-card planarity or contact signature. The card isn't landing cleanly at the edge of the wafer."
   - *Optional:* switch the wafer filter to any `None`-class wafer from the table for contrast.
5. **Classifier vs Engineer Review:**
   > "How much should you trust the classifier? Yield engineers labeled 521 wafers by eye. The ML classifier, a random forest trained on those labels and registered in Unity Catalog, agrees on **97%**, scored on wafers it never saw during training, and on all 40 Edge-Ring wafers. The hand-written rules we started with got 93% on the same wafers. Next to it, an unsupervised anomaly detector scores how unusual every map is, so a wafer that matches no known pattern still lands in the engineers' queue."

**10-minute cut:** skip step 5 here, and skip section 5 (Genie) entirely.

---

## 4. Act on it: the Wafer Operations desk on Lakebase (3 minutes)

**Click:** the **Wafer Operations** tab.

> "Analytics tells you what happened. Operations needs somewhere to *act*, with transactional speed. This desk runs on **Lakebase**, Databricks' serverless Postgres. The pipeline syncs the gold wafer patterns and die maps into Postgres, and the app reads them in milliseconds. Look at the green chip: the queue loaded in a few milliseconds."

1. **Filter the queue:** Pattern = **Edge-Ring**, Site = **AUS**.
   > "Here's the Edge-Ring queue at Austin: the PC-AUS-447 wafers we just found on the dashboard."
2. **Select a wafer**, e.g. `MX7-F12-W27-W01`.
   > "The die map renders straight from Postgres: 529 dies, 87 failing, ringed around the edge, mostly HB_021. Same wafer, same evidence, but now in an operational tool. The facts panel shows what the models said: Edge-Ring at 99.8% confidence, and an anomaly score of 0.65, well above the flag threshold."
3. **Record a disposition:** type a note such as *"PC-AUS-447 pulled for planarity check"* and click **Hold**.
   > "That's a real write to Postgres, an OLTP transaction recorded with my identity and a timestamp, not a ticket in another system. It shows in the history immediately, and the wafer stays in the open queue until someone releases, re-probes or scraps it."
4. *Optional:* switch the status filter to **All flagged** to show dispositioned wafers.

**Point to make:**
> "Same governed data, two speeds: Delta for analytics and AI, Lakebase for the operational workflow. No separate database team, no nightly extract."

---

## 5. Ad-hoc questions: Genie (3 minutes)

**Click:** the Genie tab.

> "Dashboards answer the questions you thought of in advance. Genie answers the next one, in plain English, against **governed metric views**. FPY is defined once, die-weighted, in Unity Catalog, so Genie, the dashboard and any notebook all get the same number."

Ask, in order:

1. *"How many Edge-Ring wafers did each AUS tester produce in August 2025?"*
   - Expect TST-AUS-05, 03 and 04 at the top.
2. *"Compare the average contact resistance (PT_0210) z-score before and during the anomaly at AUS for MX-7."*
   - Expect a clear positive z-shift during the event.
3. *"What is the wafer-map pattern mix by week for MX-7 at Austin?"*
4. Ask the audience for a question, if the room is engaged.

**Point to make:** point at the SQL shown under each answer.
> "Every answer shows the SQL it ran. This one is querying `mv_stdf_wafer_patterns`, a metric view with the business logic baked in. No hallucinated joins."

---

## 6. Root cause in one question: Supervisor Agent (3 minutes)

**Click:** the Supervisor Agent tab.

> "Now the real war-room question. It needs both numbers *and* context: what happened, why and what fixed it."

Ask:
> *"Which testers and probe card produced Edge-Ring wafers at Austin between Aug 18 and Aug 27 2025, and what change log entries explain it?"*

**What happens (narrate while it runs):**
> "This is a multi-agent supervisor built with Agent Bricks. It's routing: the counts go to the **Genie agent** as live SQL, and the 'why' goes to a **Knowledge Assistant** grounded on our data dictionary and change-log documents."

**Expected answer:**
- **Live data from Genie:** TST-AUS-05 had 14 Edge-Ring wafers, TST-AUS-03 had 8 and TST-AUS-04 had 6, all on PC-AUS-447, at about 86–87% average yield.
- **Context from the KA:**
  - root cause is PC-AUS-447 Rev C plus HF-3.2.1;
  - HB_021 contact failures at the edge;
  - retest rate and UPH impact;
  - resolved by the Aug 24 22:15 rollback.

> "That took one question. In a typical fab it's a day of pulling reports from three systems."

Optional follow-ups:
- *"What does hard bin HB_021 mean and which wafer patterns usually cause it?"* This one is KA only.
- *"What was the total cost impact at Austin and when did it stop growing?"* This one is Genie only.

---

## 7. Under the hood: one pipeline, one source of truth (3 minutes)

**Click:** the **Data Pipeline** tab.

> "Here's the part architects care about. Everything you just saw comes from **one schema**, built by **one pipeline**, and the business can refresh it from right here."

1. **The steps** (Lakeflow Job `[mfg_ops] STDF pipeline - ingest, transform, serve`):
   > "Generate the STDF files, **ingest them with a Lakeflow declarative pipeline**, using Auto Loader with data-quality expectations on every feed. Then transform to silver and gold, including the wafer-map classifier. Then refresh the **Lakebase** serving tables, regenerate the Knowledge Assistant's documents and re-sync the agents. If the data changes, the dashboard, the wafer desk, Genie *and* the AI's knowledge all update together."
   - Point at **Refresh data**. Don't click it live: a full run is about 25 minutes. Show the last successful run instead, and click through to it with **Open job** if the audience wants to see the Lakeflow UI.
2. **Data quality:** in the workspace, open the pipeline `[mfg_ops] STDF raw ingestion (Lakeflow)`.
   > "Every raw feed has expectations: missing keys and impossible die coordinates are dropped, unknown sites or bins are flagged. This run ingested all 565,856 die results with zero drops."

**Switch to:** the workspace, Catalog Explorer → `parijat_demos.mfg_ops`.
3. **Lineage:** open `gold_wafer_patterns` → **Lineage**.
   > "Full column-level lineage, from raw PRR die results to the pattern class, the metric view Genie queries, and the Lakebase synced table the ops desk reads."
4. **Governance:**
   > "One set of Unity Catalog permissions covers tables, metric views, the volume of documents, the Lakebase synced tables, the dashboard, Genie and the agents."
5. **It's code** (optional: show `databricks.yml`):
   > "The schema, warehouse, Lakeflow pipeline and job, Lakebase project and synced tables, dashboard and Genie space are all one Databricks Asset Bundle, in Git. `bundle deploy` to a new workspace and you have the whole thing."

---

## 8. Close (1 minute)

> "So: a seven-point yield excursion, from first symptom to probe-card root cause, wafer-map evidence, dollar impact and an engineer's hold on the affected wafers, on one platform.
>
> For you, the questions are: where does your STDF land today, how long does a yield excursion take to close, and what would it be worth to catch the next PC-AUS-447 in hours instead of days?"

**Suggested next step:** a 2-hour working session on a sample of their STDF. The `raw_stdf_*` tables are the contract, so a real parser (e.g. pySTDF on Lakeflow) slots in front of the same pipeline.

---

## Likely questions and answers

| Question | Answer |
|---|---|
| *Is this real STDF?* | The data is synthetic but follows the STDF record structure: PRR for die results and X/Y, PTR for parametric tests. Real STDF parses into the same `raw_stdf_*` tables with an open-source parser (pySTDF) in a Lakeflow pipeline. |
| *Why rules and not deep learning for wafer maps?* | Explainability and zero model ops for a first deployment. The labels and die maps are already in UC, so a CNN or gradient-boosted model in MLflow is a drop-in, scored against the same `gold_wafer_pattern_eval` harness. |
| *How does this scale?* | This demo is about 566K die results. Production fabs generate billions per month. It's the same Delta, serverless SQL and Photon stack: partition or cluster by date/site/product and use incremental (Lakeflow) ingestion. |
| *Can it alert us before engineers notice?* | Yes. Add a SQL alert or Lakehouse Monitoring on the daily Edge-Ring rate per probe card. In this story it would have fired on Aug 18. |
| *Does Genie make things up?* | It's grounded on metric views with certified definitions and example SQL, and every answer shows its query. Instructions constrain formatting and semantics. |
| *Security for the app?* | Genie and the agent run on-behalf-of the signed-in user, so UC permissions apply to them. The Wafer Operations desk and Data Pipeline tab use the app's service principal, which has only read on the synced schema, its own write-back schema, and run permission on the one job. Every disposition records who entered it. |
| *Why Lakebase and not just the warehouse?* | The ops desk needs millisecond point lookups and transactional writes from many users. Lakebase is serverless Postgres that scales to zero, and synced tables keep it in step with the gold layer without a separate ETL. |
| *Can dispositions flow back to analytics?* | Yes: Lakehouse sync (CDC from Lakebase to Delta) would land `wafer_dispositions` in Unity Catalog, so Genie could answer "how many Edge-Ring wafers were scrapped". It's the natural next step. |
| *What did it take to build?* | One bundle: about 700 lines of generator, about 850 lines of SQL, an 80-line Lakeflow pipeline, a dashboard JSON, a Genie JSON and the Lakebase definitions. It redeploys with `bundle deploy` plus one job run (~25 minutes). |

---

## Reset after the demo

- Clear the dashboard filters, and set the wafer map back to `MX7-F12-W27-W01` if you changed it.
- Dispositions you record in Wafer Operations persist, and that's intentional. Use a different wafer each time if you want an empty history on screen.
- No data changes are needed. To rebuild everything, press **Refresh data** in the Data Pipeline tab, or run `databricks bundle run demo_workflow -p e2-demo-fe`.

## Key numbers cheat sheet

| Metric | Value |
|---|---|
| AUS MX-7 FPY, baseline → incident | 95.4% → 88.3% (daily low 86.5%, 08-19) |
| HB_021 share | 1.5% → 8.7% |
| Retest rate | 2.5% → 9.5% |
| AUS MX-7 UPH | ~590 → ~400 |
| Incident cost (Aug 18–27, AUS MX-7) | ~1,100 lost dies, ~$17.6K |
| Edge-Ring incident wafers | 30 (TST-AUS-05: 14, -03: 8, -04: 8), all PC-AUS-447 |
| Showcase wafer | MX7-F12-W27-W01: 83.6% yield, 39.5% edge fail vs 10.1% center |
| Classifier agreement | 97.1% of 521 reviewed wafers, cross-validated (rules baseline 93.1%) |
| Anomaly detector | ROC AUC 0.988; 379 of 1,200 wafers flagged |
| Onset / recovery | 2025-08-18 07:30 / 2025-08-24 22:15 |
| Lakebase reads (serverless, measured) | queue ~17 ms, one wafer's 529-die map ~18 ms |
| Lakeflow ingest | 565,856 PRR + 487,320 PTR rows, 0 dropped by expectations |
