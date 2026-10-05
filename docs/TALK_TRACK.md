# Manufacturing Operations Center: Demo Talk Track

**Audience:** semiconductor and electronics manufacturing (yield/test engineering, manufacturing IT, data and analytics leaders)
**Length:** about 20 minutes, plus Q&A. There are cut points for a 10-minute version.
**Story in one line:** *A probe-card change quietly cost seven points of yield. Watch how fast we go from "FPY looks off" to "it's PC-AUS-447 on three testers, here's the dollar impact, and here's the wafer-map proof", without leaving one governed platform.*

---

## Pre-demo checklist (15 minutes before)

- [ ] Open the app at https://mfg-ops-center-1444828305810485.aws.databricksapps.com and click through all three tabs.
  - If Genie or Agent returns 403, the OAuth consent has gone stale. See DESIGN.md §7.
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

**Show:** the app landing page and its three tabs.

> "This is a Databricks App, a full web application running inside the platform. It's secured by the same identity, and every query runs *as the signed-in user*, so Unity Catalog permissions are enforced end to end. There's no extract and no separate BI server. It has three views: an operational dashboard, Genie for ad-hoc questions, and an AI supervisor agent."

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
   > "And the Edge-Ring wafers aren't spread across the floor. They're on **TST-AUS-05, 03 and 04**, the three testers that got the new probe card. Every one of the 28 incident Edge-Ring wafers ran on **PC-AUS-447**."
4. **Wafer Map** (defaults to `MX7-F12-W27-W01`):
   > "Here's one of them: wafer 1 of lot MX7-F12-W27, probed on TST-AUS-05 on the 18th, at 83.6% yield. You can see the ring. Almost **40% of edge dies fail**, against about 10% in the center, and most edge failures are HB_021. That's a classic probe-card planarity or contact signature. The card isn't landing cleanly at the edge of the wafer."
   - *Optional:* switch the wafer filter to any `None`-class wafer from the table for contrast.
5. **Classifier vs Engineer Review:**
   > "How much should you trust the classifier? Yield engineers labeled 521 wafers by eye, and the classifier agrees on **93%**, with 37 of 40 on Edge-Ring. It's deliberately an explainable rule set in SQL: radial zone fail rates, cluster shape, angular concentration. An engineer can read exactly why a wafer was called Edge-Ring. Swapping in an ML model later is a drop-in, because the labeled training set is already in the lakehouse."

**10-minute cut:** skip step 5, and skip section 4 entirely.

---

## 4. Ad-hoc questions: Genie (3 minutes)

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

## 5. Root cause in one question: Supervisor Agent (3 minutes)

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

## 6. Under the hood: one pipeline, one source of truth (3 minutes)

**Switch to:** the workspace, Catalog Explorer → `parijat_demos.mfg_ops`.

> "Here's the part architects care about. Everything you just saw comes from **one schema**, built by **one pipeline**."

1. **The job** (`[DEMOGEN] - semi_stdf - ...`) has four tasks:
   > "Ingest the STDF results, transform to silver and gold including the wafer-map classifier, regenerate the Knowledge Assistant's documents from the gold tables, and re-sync the agents. If the data changes, the dashboard, Genie *and* the AI's knowledge all update together. The KA can't drift from the data, because the pipeline writes its corpus."
2. **Lineage:** open `gold_wafer_patterns` → **Lineage**.
   > "Full column-level lineage, from raw PRR die results to the pattern class to the metric view Genie queries."
3. **Governance:**
   > "One set of Unity Catalog permissions covers tables, metric views, the volume of documents, the dashboard, Genie and the agents."
4. **It's code** (optional: show `databricks.yml`):
   > "The schema, warehouse, job, dashboard and Genie space are all one Databricks Asset Bundle, in Git. `bundle deploy` to a new workspace and you have the whole thing."

---

## 7. Close (1 minute)

> "So: a seven-point yield excursion, from first symptom to probe-card root cause, wafer-map evidence and dollar impact, on one platform.
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
| *Security for the app?* | The app runs on-behalf-of the user, so UC row- and column-level security applies to every tab. |
| *What did it take to build?* | One bundle: about 700 lines of generator, about 850 lines of SQL, a dashboard JSON and a Genie JSON. It redeploys with `bundle deploy` plus one job run (~25 minutes). |

---

## Reset after the demo

- Clear the dashboard filters, and set the wafer map back to `MX7-F12-W27-W01` if you changed it.
- No data changes are needed. If you want fresh agent state, rerun the job: `databricks bundle run demo_workflow -p e2-demo-fe`.

## Key numbers cheat sheet

| Metric | Value |
|---|---|
| AUS MX-7 FPY, baseline → incident | 95.4% → 88.3% (daily low 86.5%, 08-19) |
| HB_021 share | 1.5% → 8.7% |
| Retest rate | 2.5% → 9.5% |
| AUS MX-7 UPH | ~590 → ~400 |
| Incident cost (Aug 18–27, AUS MX-7) | ~1,100 lost dies, ~$17.6K |
| Edge-Ring incident wafers | 28 (TST-AUS-05: 14, -03: 8, -04: 6), all PC-AUS-447 |
| Showcase wafer | MX7-F12-W27-W01: 83.6% yield, 39.5% edge fail vs 10.1% center |
| Classifier agreement | 93.1% of 521 reviewed wafers |
| Onset / recovery | 2025-08-18 07:30 / 2025-08-24 22:15 |
