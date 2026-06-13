# CarbonMetric
Plataforma de Inventário de Gases do Efeito Estufa (IGEE) — GHG Protocol.

## Rodar localmente
```bash
pip install -r requirements.txt
python app.py
```

## Deploy no Render
1. Suba este repositório no GitHub
2. Crie um Web Service no Render conectado ao repositório
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`
5. Adicione a variável `DATABASE_URL` com a URL do PostgreSQL
