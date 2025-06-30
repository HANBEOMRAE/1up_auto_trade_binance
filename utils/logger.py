import datetime

class Logger:
    def log(self, message):
        print(f"[{datetime.datetime.now()}] {message}")