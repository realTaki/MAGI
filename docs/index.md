---
title: MAGI — toward recursive self-improvement
description: A multi-agent runtime built toward recursive self-improvement.
home: true
---

<section class="hero">
  <div class="eyebrow">Modular Agentic Genesis Intelligences</div>
  <h1>Agents that can improve together.</h1>
  <p>MAGI is an experimental multi-agent runtime. Each agent has its own identity, memory, tools, and workspace. The installation has an editable source checkout. The research goal is recursive self-improvement: propose changes to the system, evaluate the results, and build on what works.</p>
  <div class="hero-actions">
    <a class="button primary" href="{{ '/architecture/' | relative_url }}">Explore the architecture</a>
    <a class="button secondary" href="{{ '/terms/' | relative_url }}">Learn the language</a>
  </div>
</section>

<section class="section">
  <h2>From persistent agents to self-improvement</h2>
  <p class="section-intro">Each MAGI has its own runtime and workspace. ASP starts the process and relays sessions. The source is locally editable; autonomous validation and adoption of changes remain a research goal.</p>
  <div class="card-grid">
    <a class="card" href="{{ '/architecture/' | relative_url }}">
      <h3>Architecture</h3>
      <p>Understand the BUS boundary, durable state, composition roots, workers, and runtime invariants.</p>
    </a>
    <a class="card" href="{{ '/business-flows/' | relative_url }}">
      <h3>Behavioural guardrails</h3>
      <p>Read the business flows and behaviour that changes must preserve.</p>
    </a>
    <a class="card" href="{{ '/terms/' | relative_url }}">
      <h3>Terms and identifiers</h3>
      <p>Get the shared vocabulary for MAGI, ASP, BUS, Books, and Jobs. EVA is the handle naming pattern.</p>
    </a>
    <a class="card" href="{{ '/roadmap/' | relative_url }}">
      <h3>Roadmap</h3>
      <p>See the forward-looking work, its status, and the decisions still open.</p>
    </a>
  </div>
</section>

<section class="section">
  <h2>Design principles</h2>
  <ul class="principles">
    <li><strong>Persistence is foundational.</strong> A MAGI should carry continuity beyond a single task or process.</li>
    <li><strong>Governance is mandatory.</strong> Autonomy remains observable, bounded, and accountable to operators.</li>
    <li><strong>Coordination is protocol-mediated.</strong> Infrastructure defines safe ways to discover, communicate, and delegate without prescribing every reasoning step.</li>
    <li><strong>Build for abundant intelligence.</strong> Design around durable coordination problems, not temporary model limitations.</li>
    <li><strong>Evaluate improvement.</strong> State what a change should accomplish, inspect the result, and retain it only when it helps.</li>
  </ul>
</section>

<section class="section">
  <h2>Start here</h2>
  <p class="section-intro">New to MAGI? Begin with the <a href="{{ '/terms/' | relative_url }}">terminology</a>, then follow the <a href="{{ '/architecture/' | relative_url }}">runtime architecture</a>. For the reasoning behind the project, read <a href="{{ '/insights/designing-for-abundant-intelligence/' | relative_url }}">Designing for Abundant Intelligence</a>.</p>
</section>
