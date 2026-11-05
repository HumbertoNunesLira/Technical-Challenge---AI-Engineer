# Technical Challenge — AI Engineer (Artefact)

Customer support agent for **Empório da Música**, a fictional musical instruments store.

The project lives in [`emporio-agent/`](emporio-agent/). Start with its
[README](emporio-agent/README.md): quick start, architecture, technical decisions,
assumptions, privacy and safety, evaluation, limitations and use of AI assistants.

- Example conversations: [`emporio-agent/examples/`](emporio-agent/examples/)
- Scenario suite: [`emporio-agent/tests/scenarios.yaml`](emporio-agent/tests/scenarios.yaml)

```bash
cd emporio-agent
python -m venv .venv
.venv\Scripts\activate             # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env             # macOS/Linux: cp .env.example .env; then set ANTHROPIC_API_KEY
python -m emporio chat
```
