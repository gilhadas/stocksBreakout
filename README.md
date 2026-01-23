# Swing trading (paper account)
python breakout_scanner.py watchlist.txt --mode swing

# Daytrade (15min bars, paper)
python breakout_scanner.py watchlist.txt --mode daytrade

# Live account (BE CAREFUL!)
python breakout_scanner.py watchlist.txt --mode swing --live

# Custom parameters
python breakout_scanner.py watchlist.txt --mode swing --vol 1.5 --atr 0.8
