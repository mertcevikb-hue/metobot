module.exports = {
  apps: [
    {
      name: "metobot-web",
      script: "python",
      args: "-m uvicorn web.api:app --host 0.0.0.0 --port 8000",
      cwd: "./",
      autorestart: true
    },
    {
      name: "metobot-trainer",
      script: "python",
      args: "bot1_trainer.py",
      cwd: "./",
      autorestart: true
    },
    {
      name: "metobot-live",
      script: "python",
      args: "bot2_live.py",
      cwd: "./",
      autorestart: true
    }
  ]
};