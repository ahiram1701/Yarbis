import json

# Leer mi state para obtener la key
with open('state.json', encoding='utf-8') as f:
    my_state = json.load(f)

api_key = my_state['ollama']['api_key']
print(f"API key length: {len(api_key)}")
print(f"API key starts: {api_key[:10]}...")

# Config a copiar
ollama_config = {
    "model": "deepseek-v4-flash:cloud",
    "fallback_models": [],
    "host": "https://api.ollama.com",
    "api_key_env_var": "OLLAMA_API_KEY",
    "timeout_seconds": 900,
    "api_key": api_key
}

instances = ['trader', 'social', 'jairo', 'clodex']
base = r'C:\DEV\tests\yarbis\.yarbis_instances'

for inst in instances:
    path = f'{base}\\{inst}\\state.json'
    with open(path, encoding='utf-8') as f:
        s = json.load(f)
    s['ollama'] = ollama_config.copy()
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(s, f, indent=2, ensure_ascii=False)
    # Verify
    with open(path, encoding='utf-8') as f:
        v = json.load(f)
    key_ok = v['ollama']['api_key'] == api_key
    ok_str = 'OK' if key_ok else 'FAIL'
    print(f"{inst}: api_key {ok_str} (len={len(v['ollama']['api_key'])})")
