# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Seven Seas Petroleum (SSP)'s business development / bidding team — the people
who decide which tenders SSP pursues and prepare bids. They authenticate with
their normal SSP Active Directory (Windows/email) username and password.

## Product Purpose

TenderIQ centralizes tender opportunities from SSP's supplier procurement
portals into one dashboard, so the bidding team doesn't have to log into and
check each portal separately, and uses AI to score and summarize tenders to
help the team prioritize which ones to pursue.

## Positioning

Unlike manually logging into each procurement portal in turn, TenderIQ
automatically scrapes and centralizes tenders — including invited/"My
Tenders" opportunities that are easy to miss without checking every portal
individually — then applies AI scoring and summarization to help the team
triage what's worth pursuing. It catches opportunities that would otherwise
be missed and saves time versus the manual multi-portal routine, in roughly
equal measure.

## Operating Context

Runs on SSP's internal network only (intranet-only; never exposed to the
public internet). Employees authenticate via LDAP against SSP's Active
Directory. Currently run from a team member's laptop; moving to a shared,
always-on server on SSP's network is in progress (server already updated to
bind all interfaces and start headless for this). A scheduled task also runs
the scraper automatically, independent of dashboard-triggered runs.

## Capabilities and Constraints

- Scrapes tender listings from PDO SRM (srm.pdo.co.om) and OQ Tawreed
  (tawreed.oq.com), including invited/"My Tenders" opportunities not shown
  on public listings.
- AI-assisted scoring and summarization of tenders via the Anthropic API
  (called server-side; the API key never reaches the browser).
- SOW (scope of work) document extraction from fetched tender documents.
- In-dashboard credential rotation for the PDO/OQ portal login credentials,
  with changes audit-logged.
- More tender-source portals are expected to be added over time —
  undecided which ones yet. Design and copy should not assume there will
  always be exactly two.

## Brand Commitments

Name: "TenderIQ". Current page title/tagline in use is "PDO Tender
Intelligence" — worth revisiting once portals beyond PDO/OQ are added, since
it names only one source. Not changed now; flagging as an open item.

## Evidence on Hand

Real historical scrape results from PDO SRM and OQ Tawreed live in
`tenders.json`. No testimonials, case studies, or external marketing
evidence exist or are needed — this is an internal tool for one company's
own team, not a marketed product.

## Product Principles

1. Never miss a real opportunity — invited/hard-to-find tenders matter as
   much as public listings.
2. One place instead of many — the dashboard should replace, not
   supplement, manually checking individual portals.
3. Built for a small internal team, not the public — favor operator
   efficiency and scanability (Operate mode) over persuasion or marketing
   polish.
4. Extensible to more portals — avoid designs, labels, and copy that bake
   in an assumption of exactly two sources forever.
