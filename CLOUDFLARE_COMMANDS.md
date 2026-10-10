# Cloudflare Tunnel Quick Reference

### 1. One-Click Double-Click Scripts
- **Start Tunnel:** Double-click `start_tunnel.bat`
- **Stop Tunnel:** Double-click `stop_tunnel.bat`

---

### 2. Manual Commands (PowerShell)
- **Start:**
  ```powershell
  .\cloudflared.exe tunnel --url http://localhost:8000
  ```
- **Stop:**
  ```powershell
  Stop-Process -Name cloudflared -Force -ErrorAction SilentlyContinue
  ```

---

### 3. Manual Commands (Command Prompt / CMD)
- **Start:**
  ```cmd
  cloudflared.exe tunnel --url http://localhost:8000
  ```
- **Stop:**
  ```cmd
  taskkill /IM cloudflared.exe /F
  ```
