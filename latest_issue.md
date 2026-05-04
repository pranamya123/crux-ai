# The Weekly Signal - Issue 1, Apr 28 - May 4, 2026

5 company items, 0 research items. The action this week sat at the infrastructure layer: open weights, cheaper silicon, and forward-deployed services taking the spotlight from any single model release.

## What shipped: 5 company items

### 1. Mistral ships open-weights 128B model with 256K context
Mistral Medium 3.5 merges instruction-following, reasoning, and coding into a single 128B dense model under a modified MIT license. It scores 77.6% on SWE-Bench Verified and 91.4 on τ³-Telecom, with configurable per-request reasoning effort and a vision encoder trained from scratch for variable image sizes. The weights self-host on as few as four GPUs, and the API runs $1.50/$7.50 per million input/output tokens. It launched alongside Vibe, a set of async cloud coding agents, and a new Work mode in Le Chat for multi-step tasks. If you have been pinned to closed flagships for SWE-bench-grade code work, pull the weights this week and benchmark them against whatever you are currently paying for. [Link](https://mistral.ai/news/vibe-remote-agents-mistral-medium-3-5)

### 2. Tenstorrent's Blackhole ships at $110K per 23-PFLOPS server
Tenstorrent hit general availability on Galaxy Blackhole, a 6U server packing 32 RISC-V Blackhole chips. The box delivers 23 PFLOPS Block FP8, 1 TB of DRAM at 16 TB/s, and 56 by 800G Ethernet ports. In Blitz Mode it hits 350+ tokens per second per user on DeepSeek R1-0528 671B and renders a 5-second 720p video in 2.4 seconds, which Tenstorrent claims is 10x faster than leading GPU systems. Pricing starts at $110,000 per server or $440,000 for a 4-system supercluster. If your inference economics have stalled waiting on H200 or B200 quotes, request access and run your hottest workload through it before signing another GPU contract. [Link](https://www.hpcwire.com/aiwire/2026/05/01/tenstorrent-announces-general-availability-of-galaxy-blackhole-ai-system/)

### 3. Anthropic spins up a $1.5B forward-deployed services firm
Anthropic, Blackstone, and Hellman & Friedman each committed $300M to a new $1.5B AI-native services company that embeds Anthropic engineers inside mid-sized customers to build custom Claude deployments. Apollo Global, General Atlantic, GIC, Leonard Green, and Sequoia round out the consortium. The model mirrors Palantir's forward-deployment playbook but pairs it with ownership of the underlying frontier model, a vertical-integration move no other frontier lab has tried at this scale. If you run a mid-market company stuck in Claude pilot purgatory, this is the new reference for what a real production rollout looks like. Send the link to whoever owns AI strategy before your next vendor pitch. [Link](https://www.anthropic.com/news/enterprise-ai-services-company)

### 4. SAP buys Prior Labs to build a European tabular-foundation lab
SAP signed a definitive agreement to acquire Prior Labs, the team behind TabPFN, and committed over €1 billion over four years to run it as an independent frontier AI lab focused on structured business data rather than language. TabPFN-2.6 currently tops TabArena, the leading tabular foundation model benchmark, and Prior Labs claims it matches the accuracy of a four-hour AutoML pipeline in a single instant forward pass. The open-source TabPFN tool has crossed 3 million downloads. If you do churn, fraud, or supplier-risk modeling and still reach for XGBoost by default, swap in TabPFN-2.6 on a holdout set this week and log the deltas. [Link](https://news.sap.com/2026/05/sap-to-acquire-prior-labs-establish-frontier-ai-lab-europe/)

### 5. Claude gets official MCP connectors for Blender, Fusion, Ableton
Anthropic shipped MCP-based connectors linking Claude directly to Blender, Autodesk Fusion, Adobe Creative Cloud, Ableton, Splice, and Affinity by Canva. The Blender team built their official connector themselves. Inside each tool Claude can debug scenes, write custom scripts, batch-apply changes across objects, ground answers in official documentation, and translate assets between applications. Initial educational pilots are running with RISD, Ringling, and Goldsmiths. If you produce 3D, audio, or design assets at any volume, install the relevant connector and write one batch script for a task that normally costs an afternoon of clicking. The first ten minutes will tell you whether the rest of your pipeline belongs in MCP. [Link](https://www.testingcatalog.com/anthropic-rolls-out-claude-connectors-for-creative-platforms/)

## Worth knowing: 0 research findings

Nothing cleared the bar this week, and no prior issues exist to carry forward (this is Issue 1). Back next week with fresh papers.

## Your 4-hour build this week

Pull Mistral Medium 3.5's open weights, host them on four GPUs or hit the API at $1.50 per million input tokens, and wire the model into a small SWE-bench-style harness pointed at your own repo. Pick 10 real bugs from your issue tracker, hand the model the failing tests plus the relevant files, and measure pass rate and tokens used. Success: a one-page comparison table against whichever closed model you are paying for today, with concrete pass/fail counts and a per-bug cost figure.