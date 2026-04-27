---
title: "About"
description: "Kesten Broughton — staff security engineer, CVE researcher, FWD:cloudsec speaker, CIS benchmark contributor. Writing about secure AI development patterns."
date: 2026-04-27
layout: page
url: /about/
---

## Security engineering in a World Full of Bots

I'm a staff security engineer with 15 years of experience finding the gaps between what people think their systems do and what those systems actually do. That gap has never been bigger than it is right now, with AI agents being handed credentials and told to figure it out.

That's what this blog is for.

---

## What I've done

**Speaking**
Two-time speaker at [FWD:cloudsec](https://fwdcloudsec.org), the practitioner-focused cloud security conference. I talk about real infrastructure problems, not product pitches.

**CVE research**
Found and disclosed vulnerabilities across cloud platforms and open-source tooling. When I'm not building things, I'm reading the code of things other people built and asking "what did they assume would never happen?"

**Bug bounty**
Dozens of accepted submissions across major programs. Bug bounty is how I stay honest — the finding only counts if the vendor agrees it's real.

**CIS benchmarks**
Contributor to CIS benchmark development. The benchmarks are imperfect (every practitioner argues with at least three entries), but they're the best public baseline we have, and making them better matters.

---

## What this blog covers

The common thread is the gap between **security intent and security reality** — which is widest right now in AI-adjacent infrastructure.

**Secure AI development patterns** — credential management for agents, least-privilege OAuth flows, how to scope what an AI assistant can actually read and send, what happens when a refresh token leaks and the agent has been running for six months.

**Cloud security engineering** — GCP primarily, AWS when unavoidable. IAM that doesn't require a whiteboard session to understand. Secret management that ops teams will actually use.

**Threat modeling the unglamorous parts** — email infrastructure, OAuth apps, forwarding rules, the stuff that sits one misconfiguration away from a full account takeover but doesn't make conference talks because it isn't interesting until it's an incident.

---

## What this blog is not

A place to discuss AI capabilities in the abstract. A vendor review site. A newsletter with five bullet points and a sponsored section. An optimistic take on anything.

---

## Get in touch

GitHub: [kbroughton](https://github.com/kbroughton)

If you found something wrong in a post — a factual error, a misconfiguration in the code, a security issue in the scripts — I want to know. Open an issue on the [repo](https://github.com/kbroughton/kbroughton.github.io/issues).
