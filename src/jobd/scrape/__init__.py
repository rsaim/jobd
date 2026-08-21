"""Seed-and-expand mailbox scraping (v2).

The measured alternative to a full-mailbox sweep: seed Gmail queries pull the
high-signal slices (sent threads, ATS senders, the LinkedIn InMail relay, a
phrase pack), every confident positive teaches new entities (sender domains,
addresses, threads), and entity queries expand the frontier until it is empty.
A final residual pass triages direct-addressed mail the queries never matched.

Measured on the reference mailbox (58,331 messages, 2,631 known positives):
97.1% recall fetching 20.2% of the mailbox; the residual pass closes it to
100% at 29.4%. See docs/seed-and-expand.md for the experiment series.

The pipeline is a LangGraph state machine (graph.py) because the loop is the
architecture: nodes are resumable units, the expansion cycle is an edge, and
every node streams progress events (events.py) that the live dashboard renders.
Classification itself is unchanged v1 machinery — prefilter, rule-based
extractor, LLM, sender-rule fanout — called from one node.
"""
