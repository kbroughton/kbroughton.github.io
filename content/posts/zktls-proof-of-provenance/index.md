---
title: "zkTLS: Cryptographic Proof That Web Data Is Real"
date: 2026-05-09
draft: false
tags: ["zktls", "tls", "zero-knowledge-proofs", "mpc", "reclaim-protocol", "tlsnotary", "compliance", "identity", "web3"]
description: "How zkTLS lets a user prove that specific data appeared in an HTTPS response — without revealing credentials, session keys, or the full response. Covers proxy, MPC-TLS, and TEE approaches."
canonicalURL: https://blog.pcioasis.com/posts/zkTLS/zktls-proof-of-provenance/
cover:
  image: diagram-page1.png
  relative: true
  alt: "zkTLS income verification flow — browser, attestor, ADP payroll server, and proof submission"
---

# zkTLS: Cryptographic Proof That Web Data Is Real

You can screenshot a bank statement. You can export a CSV from your payroll provider. You can copy text from any HTTPS page you have access to. What you cannot do — until recently — is prove to a third party that the data came from where you claim, without also handing them your credentials, running through a centralized KYC gateway, or asking the original server to cooperate.

zkTLS changes that. It lets a user prove — with cryptographic soundness — that a specific piece of data appeared in a specific HTTPS response, without revealing anything else: not the session key, not the full response, not the user's login credentials.

This post covers what zkTLS is, how the three main protocol families work, who is building in the space, and why the approach matters for compliance and identity use cases.

{{< figure src="zktls-flow-animation.gif" caption="End-to-end zkTLS income verification using the Reclaim Protocol. Phase 1: browser fetches the rental application page. Phase 2: TLS session routed through the Reclaim attestor to ADP's payroll server. Phase 3: ZK proof submitted directly to apartments.com — salary never leaves the browser." >}}

---

## The TLS Provenance Problem

HTTPS gives you confidentiality and integrity between a client and a server. It does not give you *exportable proof* of what the server said.

When you load your bank balance at `https://bank.com/account`, TLS guarantees that nobody in the middle tampered with the response. But once that response reaches your browser, the proof evaporates. The MAC that authenticated each record was computed with your session key — a secret you share with the bank, not with anyone else. You can forward the bytes to a third party, but they have no way to know you didn't fabricate them. The bank could confirm the data independently, but that requires them to build and maintain an API just for that purpose, which most institutions won't do.

This gap matters in a surprising number of contexts:

- **Financial underwriting**: Proving income or deposit history to a lender without screen-scraping (which requires handing over login credentials)
- **Compliance evidence**: Demonstrating that a vendor's configuration portal showed a specific control state at audit time
- **Cross-platform identity**: Proving you hold an account on platform A when registering on platform B, without OAuth cooperation from platform A
- **Credential verification**: Showing a KYC status, professional license, or certification from an existing authoritative source without that source building a dedicated proof endpoint

The underlying problem is that TLS was designed for transport security, not for attestation. zkTLS retrofits attestation onto the existing TLS infrastructure without requiring any changes to the server.

---

## Three Protocol Families

The field has converged around three distinct approaches, each with different trust, performance, and deployment tradeoffs.

### Proxy-Based (Reclaim Protocol)

The simplest architecture. A user routes their HTTPS request through an Attestor — a semi-trusted third party that acts as a transparent proxy. The Attestor sees the encrypted TLS traffic, co-signs a commitment to the response, and the user generates a zero-knowledge proof that a specific substring of the response satisfies some predicate (e.g., "balance ≥ $10,000") without revealing the full response or the session key.

The Reclaim Protocol implements this approach. Economic security comes from a decentralized attestor marketplace: attestors stake collateral, and a honeypot mechanism creates adversarial incentives to detect fake attestations. The user selects an attestor from this pool via an auction. A colluding attestor and user can fabricate a proof — but the honeypot traps them if they try.

**Tradeoff**: Simpler and faster than MPC-based approaches. Requires trusting the attestor not to collude with the user. Suitable for use cases where attestor collusion is economically irrational given the stake at risk.

{{< figure src="diagram-page1.png" caption="Big picture: the renter's browser fetches the rental application (①②), routes a TLS session through the Reclaim attestor to ADP's payroll server (③–⑥), and submits a ZK proof of income directly to apartments.com (⑧⑨) — the salary figure never leaves the browser." >}}

{{< figure src="diagram-page2.png" caption="Detail 2 — What the renter sees in the ADP modal. This cleartext data is visible locally in the browser but is encrypted in transit and never sent to the apartment backend." >}}

{{< figure src="diagram-page3.png" caption="Detail 3 — The attestor's view. The attestor co-signs a commitment to the encrypted TLS response without being able to read the salary figure. The honeypot mechanism makes fabrication economically irrational." >}}

{{< figure src="diagram-page4.png" caption="Detail 4 — ZK proof generation in the browser. The WASM circuit decrypts the response locally, checks the salary predicate, and discards the plaintext. Only the proof bytes are submitted." >}}

### MPC-TLS (DECO, TLSNotary)

The academically rigorous approach. Instead of routing traffic through an attestor, the client and a verifier jointly execute the TLS handshake using multi-party computation. The verifier co-derives the TLS MAC key without seeing it in full — so neither party can forge what the server said.

**DECO** (Cornell Tech / IC3, ACM CCS 2020) is the foundational paper. It introduces the three-party handshake (3PH): the prover holds the TLS key, but the MAC is computed jointly with the verifier via 2PC. The verifier's co-participation in the MAC computation is what makes server responses unforgeable — even by the prover. DECO also introduces selective opening: efficient substring ZK proofs using CBC-HMAC structure that require ~3 AES invocations instead of 1024 for a naïve approach.

**TLSNotary** is the production implementation of the MPC-TLS approach. The vlayer team's 2024 review paper (arXiv:2409.17670) is the most diagram-complete treatment of how the protocol actually works: garbled circuits handle the 2PC MAC computation, OT extensions keep it efficient, and the DEAP (Dual Execution with Asymmetric Privacy) protocol handles the selective disclosure phase.

**Tradeoff**: Trust-minimized — the verifier never has the session key, so collusion attacks are harder. But MPC is computationally expensive. DECO's benchmarks show a 2.85s three-party handshake over WAN plus 3–13s for ZK proof generation, before any application logic. Reclaim's proxy approach completes in under a second.

### TEE-Based

A third approach uses Trusted Execution Environments (Intel SGX, AMD SEV) to attest that a TLS client ran unmodified inside a hardware enclave. The attestation comes from the hardware vendor's root of trust rather than cryptographic MPC. Faster to implement and often faster at runtime, but inherits the TEE threat model: hardware vulnerabilities (Spectre, side-channel attacks on SGX) can break attestation guarantees. Not covered in depth here — the MPC and proxy approaches have stronger cryptographic foundations.

---

## Key Implementations

### Reclaim Protocol

The most deployed zkTLS stack as of 2026. The whitepaper (v2.0, September 2025) describes a production system handling real financial attestations. Reclaim published a hands-on tutorial at Devconnect 2025 Buenos Aires and has an active developer SDK. The decentralized attestor network is live. The proof system uses PLONK/Groth16 for the ZK layer.

The proxy architecture is well-suited to high-throughput use cases where the honesty assumption on attestors is economically well-founded — which covers most consumer fintech and identity use cases.

### TLSNotary + vlayer

TLSNotary is the reference implementation for MPC-TLS. vlayer is the main production builder on top of it — their GitHub Contribution Verifier demo (presented at Devconnect 2025) shows a live on-chain attestation of a GitHub contribution record using TLSNotary proofs. The vlayer team's comprehensive review paper is the best single document for understanding what the protocol is actually doing at the circuit level.

### Brave DiStefano

Brave Research's approach (December 2024) attacks the problem from the browser side. DiStefano (Designated-Commitment TLS / DCTLS) extends the TLS handshake so that commitments to session data are generated at the browser level for a designated verifier. This avoids the need for any SDK or proxy — the proof capability is built into the browser itself. Early-stage but the only approach targeting browser-native zkTLS without a separate client.

### Primus Labs

Focuses on the cryptographic efficiency layer — specifically Ferret OT (Oblivious Transfer) and QuickSilver for ZK proof generation, which are relevant to making MPC-TLS fast enough for real-world use. Presented at zkTLS Day 2025.

---

## The Competitive Landscape

The zkTLS Day conference at Devconnect 2025 (Buenos Aires, November 19) brought together all four major protocol teams — vlayer, Primus Labs, Reclaim, and TLSNotary — for the first time as a coordinated field. The key tension in the space is the proxy/MPC tradeoff: Reclaim optimizes for performance and deployment simplicity; TLSNotary optimizes for cryptographic soundness.

Both approaches solve the same user problem. The Shoal Research overview (March 2025) frames the use-case landscape clearly: a Nike verifying marathon participation from a running app, an Uber Eats verifying purchase history from a restaurant platform for cross-app incentives. The value is always the same — a user proves something about their Web2 data to a Web3 (or Web2) relying party, without that relying party needing an API relationship with the original data source.

A 2025 paper (Singh et al.) provides the formal security treatment: if an adversary can forge zkTLS proofs, they must break either TLS PRF security, SNARK soundness, or the notary's signing key. The three hardness assumptions are independent, making forgery require breaking multiple cryptographic foundations simultaneously.

---

## Research Frontier: From Data Content to Certificate Identity

The space is moving beyond proving *what a server said* toward proving *who the server is*. The zk-X509 paper (Tokamak Network, March 2026, arXiv:2603.25190) proposes zero-knowledge proofs over the existing X.509 PKI — 4 billion active certificates globally — for decentralized on-chain identity without centralized KYC, NFC scanners, or new DID infrastructure.

This is a natural extension: if zkTLS proves the content of a response, zk-X509 proves the identity of the server that produced it. Combining both gives end-to-end provable claims: "server `bank.com` (verified by chain of trust from a root CA) returned this specific data at this time, and the following predicate over that data is true."

---

## Why It Matters for Compliance Work

For teams working in compliance-heavy environments, zkTLS opens a few practical possibilities that are currently expensive or impossible:

**Audit evidence without access grants**: A vendor can prove their compliance portal showed a specific control state at a specific time without granting the auditor direct access to the portal. The proof is generated at the time of the audit window and attached to the evidence package.

**Automated control monitoring**: A monitoring system can generate continuous zkTLS proofs that a third-party service's configuration endpoint is returning the expected values — creating a cryptographically verifiable audit trail without requiring the vendor to build a dedicated attestation API.

**Third-party risk without data sharing**: A relying party can verify that a counterparty holds certain credentials (SOC 2, PCI certification status, insurance coverage) from the authoritative source's web interface, without the counterparty exporting or sharing the underlying document.

None of these are production-ready today in a turnkey form — but Reclaim's SDK is close for the straightforward data-content cases, and the cryptographic foundations are solid.

---

## Summary

| | Proxy (Reclaim) | MPC-TLS (DECO/TLSNotary) | TEE |
|---|---|---|---|
| **Trust model** | Attestor economic stake | Cryptographic MPC | Hardware vendor |
| **Performance** | Fast (<1s) | Slower (3–15s+) | Fast |
| **Forgery resistance** | Economic | Cryptographic | Hardware + crypto |
| **Deployment** | SDK, live | SDK, live | Implementation-dependent |
| **Best for** | Consumer fintech, identity | High-value, trust-minimized | Specific hardware contexts |

The TLS provenance problem has existed since HTTPS became the default transport for everything. zkTLS is the first serious attempt to solve it without requiring server-side changes. The proxy approach is deployable now; the MPC approach is approaching deployable performance. The field is moving fast — the formal security proofs are solid, the implementations are catching up.

---

*Sources: Reclaim Protocol Whitepaper v2.0 (2025); DECO — Fan Zhang et al., ACM CCS 2020 (arXiv:1909.00938); TLSNotary Review — Kalka & Kirejczyk, vlayer Labs (arXiv:2409.17670); zkTLS Day, Devconnect 2025; Shoal Research zkTLS Overview (March 2025); DiStefano Protocol, Brave Research (December 2024); zk-X509 — Bak, Tokamak Network (arXiv:2603.25190, March 2026); Singh et al., zkTLS formal security (December 2025).*
