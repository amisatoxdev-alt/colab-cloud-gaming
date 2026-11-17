import torch
import datetime

# Minimal VRAM allocation (a few MB) and small GPU computation to keep kernel active
t = torch.randn((1024, 1024), device='cuda')
res = torch.matmul(t, t).sum().item()
vram_mb = torch.cuda.memory_allocated() / (1024 * 1024)
print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Keep-Alive Pulse: OK | Result: {res:.2f} | VRAM: {vram_mb:.2f} MB")
del t
torch.cuda.empty_cache()

