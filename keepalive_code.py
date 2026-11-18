import datetime

# Emit a line BEFORE importing torch. `colab exec --timeout N` fails after N
# seconds with *no output*, and a cold `import torch` on a fresh kernel prints
# nothing for 20-40s. That silence tripped the pulse's own 20s idle timeout, the
# CLI gave up while the cell kept running server-side, and every later exec then
# queued behind it -- the exact wedge that made the launcher look dead. Printing
# first keeps the idle timer fed for the whole import.
print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] pulse: warming runtime...", flush=True)

import torch

t = torch.randn((1024, 1024), device="cuda")
res = torch.matmul(t, t).sum().item()
vram_mb = torch.cuda.memory_allocated() / (1024 * 1024)
print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Keep-Alive Pulse: OK | Result: {res:.2f} | VRAM: {vram_mb:.2f} MB", flush=True)
del t
torch.cuda.empty_cache()
