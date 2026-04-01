def clean_text(data):
    """
    Build one rich text blob per pod for embedding.
    All structured fields are already parsed — just format them clearly.
    """
    blocks = []
    for item in data:
        containers = item.get("containers") or []
        container_lines = "\n".join(
            f"  {c['name']}: state={c['state']} ready={c['ready']} "
            f"restarts={c['restart_count']} image={c['image']}"
            for c in containers
        )
        images = ", ".join(item.get("images") or []) or "unknown"

        text = f"""Namespace: {item.get('namespace')}
Pod: {item.get('pod')}
Phase: {item.get('phase')}
Status: {item.get('status')}
Node: {item.get('node')}
IP: {item.get('ip')}
QoS Class: {item.get('qos_class')}
Service Account: {item.get('service_account')}

Resources:
{item.get('resources') or 'Not available'}

CPU Usage: {item.get('cpu') or 'unknown'}
Memory Usage: {item.get('memory') or 'unknown'}
Total Restart Count: {item.get('restart_count', 0)}

Containers:
{container_lines or 'None'}

Images: {images}

Labels:
{item.get('labels') or 'None'}

Annotations:
{item.get('annotations') or 'None'}

Conditions: {item.get('conditions') or 'None'}

Events:
{item.get('events') or 'No events'}

Logs (last 300 lines with timestamps):
{item.get('logs') or 'No logs'}
"""
        blocks.append({"text": text, "metadata": item})
    return blocks
