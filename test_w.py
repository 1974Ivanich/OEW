
import tkinter as tk
import sys, os
r = tk.Tk()
r.title('TEST')
r.geometry('300x200+100+100')
tk.Label(r, text='Works!').pack()
r.after(3000, r.destroy)
r.mainloop()
