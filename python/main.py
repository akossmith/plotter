import itertools
import tkinter
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
from threading import Thread, Event
import time
import serial
from queue import Queue
import math
import typing
from gcodehandler import *

class PrinterCommander:
    def __init__(self):
        # printer physical parameters:
        #lego arms attached to metal wheel
        self.R1 = 99.625 - 3 * 7.97  # 99.625 <- last hole (one hole distance is 7.97mm)
        self.R2 = 99.625 - 3 * 7.97
        self.l1 = 159  # left wire length
        self.l2 = 159
        self.D = 258.7  # distance of motor axles

        # working area
        self.width = 80
        self.height = 80
        self.x_min = (self.D - self.width) / 2.0
        self.y_min = 15

        self.curr_alpha1 = 0.0
        self.curr_alpha2 = 0.0

        self.serial = serial.Serial('COM5', 115200, timeout=1000, parity=serial.PARITY_NONE)
        text = self.serial.readline().decode("ascii")
        print(text)
        pass

    @property
    def workspace_width(self):
        return self.width

    @property
    def workspace_height(self):
        return self.height

    @property
    def current_alphas(self) -> typing.Tuple[float, float]:
        return self.curr_alpha1, self.curr_alpha2

    def __getAlphas(self, x: float, y: float) -> typing.Tuple[float, float]:
        """x,y in printer coordinates, return: degrees"""

        # printer physical parameters:
        R1 = self.R1
        R2 = self.R2
        l1 = self.l1
        l2 = self.l2
        D = self.D

        # x = -x + self.x_min + self.width # mirroring
        # x = x * 0.8  # scaling
        # y = y * 0.8 

        x = x + self.x_min
        y = y + self.y_min
        print(f"x,y: {x:3.5f} {y:3.5f}", end=' ')

        # formulae valid for angles in [0, 180deg] (practically meaningful: [0, ~100deg])
        ca1 = (x * (R1 ** 2 - l1 ** 2 + x ** 2 + y ** 2) + y * math.sqrt(
            (-R1 ** 2 + 2 * R1 * l1 - l1 ** 2 + x ** 2 + y ** 2) * (
                    R1 ** 2 + 2 * R1 * l1 + l1 ** 2 - x ** 2 - y ** 2))) / (2 * R1 * (x ** 2 + y ** 2))
        sa1 = math.sqrt(1 - ca1 ** 2)

        ca2 = (y * math.sqrt((-D ** 2 + 2 * D * x + R2 ** 2 + 2 * R2 * l2 + l2 ** 2 - x ** 2 - y ** 2) * (
                D ** 2 - 2 * D * x - R2 ** 2 + 2 * R2 * l2 - l2 ** 2 + x ** 2 + y ** 2)) + (D - x) * (
                       D ** 2 - 2 * D * x + R2 ** 2 - l2 ** 2 + x ** 2 + y ** 2)) / (
                      2 * R2 * (D ** 2 - 2 * D * x + x ** 2 + y ** 2))
        sa2 = math.sqrt(1 - ca2 ** 2)

        alpha1 = math.atan2(sa1, ca1) / math.pi * 180
        alpha2 = math.atan2(sa2, ca2) / math.pi * 180  # by our convention positive is up (as opposed to math)
        print(f"alpha1: {alpha1:.5f} alpha2: {alpha2:.5f}")
        return alpha1, alpha2

    def move_to_alphas(self, alpha1_deg: float, alpha2_deg: float):
        # alpha1, alpha2 = alphas_deg
        print(f'requested\t l{alpha1_deg}r{alpha2_deg}')

        text = self.send_serial_command(f'move l{alpha1_deg} r{alpha2_deg}')
        left_str, right_str = text.split()
        actual_left_angle = float(left_str[2:])
        actual_right_angle = float(right_str[2:])

        print(f'actual\t\t l{actual_left_angle}r{actual_right_angle}')
        self.curr_alpha1 = actual_left_angle
        self.curr_alpha2 = actual_right_angle

    def move_to_xy(self, x: float, y: float):
        alphas = self.__getAlphas(x, y)
        self.move_to_alphas(*alphas)

    def reset_head(self):
        self.move_to_alphas(0, 0)

    def calibrate(self, alpha1_deg: float, alpha2_deg: float):
        self.send_serial_command(f"calibrate l{alpha1_deg} r{alpha2_deg}")
        self.curr_alpha1 = alpha1_deg
        self.curr_alpha2 = alpha2_deg

    def set_rpm(self, rpm: float):
        self.send_serial_command(f"setSpeed {rpm}")

    def zero_position(self):
        self.send_serial_command(f'zeroAngles')

    def send_serial_command(self, command: str) -> str:
        self.serial.write((command + "\n").encode('ascii'))
        text = self.serial.readline().decode("ascii")
        print(text)
        return text

    def burst(self, xys: typing.Iterable[typing.Tuple[float, float]]):
        def serializePointList(point_list: typing.Iterable[typing.Tuple[float, float]]):
            return "".join(f"{alpha1:06.2f}{alpha2:06.2f}"
                           for alpha1, alpha2 in point_list)

        alphass = (self.__getAlphas(x, y) for x, y in xys)
        text = self.send_serial_command("bur" + serializePointList(alphass))
        left_str, right_str = text.split()
        actual_left_angle = float(left_str[2:])  # todo: unify this
        actual_right_angle = float(right_str[2:])

        print(f'actual\t\t l{actual_left_angle}r{actual_right_angle}')
        self.curr_alpha1 = actual_left_angle
        self.curr_alpha2 = actual_right_angle

class DrawingProcess(Thread):
    def __init__(self,
                 printer: PrinterCommander,
                 filename: str,
                 interpolation_resolution: float):
        super().__init__()
        self.stop_event = Event()
        self.printer = printer
        self.drawn_points = Queue()  # todo: make this exist in main thread (bug: last segment not displayed on screen)
        self.interpolator = GCodeInterpolator(read_gcode_file(filename),
                                              max_point_distance_mm=interpolation_resolution)
        self.print = self.regular_printing

    def stop(self):
        self.stop_event.set()

    def run(self):
        self.printer.set_rpm(350)
        self.print()

    def regular_printing(self):
        for x, y in self.interpolator.xy_list_interpolated:
            if self.stop_event.is_set():
                return
            self.printer.move_to_xy(x, y)
            self.drawn_points.put((x, y))

    def burst_printing(self):
        BURST_SIZE = 5
        bursts = zip(*(self.interpolator.xy_list_interpolated[i::BURST_SIZE] for i in range(BURST_SIZE)))
        for burst in bursts:
            if self.stop_event.is_set():
                return
            self.printer.burst(burst)
            for x, y in burst:
                self.drawn_points.put((x, y))

def linear_map_to(val,
                  source_domain_lower, source_domain_upper,
                  target_domain_lower, target_domain_upper):
    return float(val - source_domain_lower) / (source_domain_upper - source_domain_lower) * \
           (target_domain_upper - target_domain_lower) + target_domain_lower

class App(tk.Tk):
    def __init__(self, canvas_width=650, canvas_height=650):
        super().__init__()

        self.canvas_width = canvas_width
        self.canvas_height = canvas_height

        self.title('Plotter')
        self.geometry(f"{canvas_width + 20}x{canvas_height + 100}")
        self.resizable(0, 0)

        self.previous_serial_command = ""
        self.current_serial_command = tkinter.StringVar()

        self.curr_xy = (0, 0)
        self.printer = PrinterCommander()

        try:
            with open("lastAlphas.txt", "r+") as f:
                alpha1, alpha2 = map(lambda x: float(x), f.readline().split())
                self.printer.calibrate(alpha1, alpha2)
                f.truncate(0)
        except:
            pass

        self.filename = tkinter.StringVar(value="../gcode/test.gcode")
        self.drawing_process = DrawingProcess(self.printer, self.filename.get(), 1)

        self.create_body_frame()
        self.create_command_frame()

        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        if self.drawing_process.is_alive():
            self.drawing_process.stop()
            self.drawing_process.join()
        if tk.messagebox.askyesno("Reset Head", "Do you want to reset printer head?", default="no"):
            self.printer.reset_head()
        with open("lastAlphas.txt", "w") as f:
            a1, a2 = self.printer.current_alphas
            f.write(f"{a1} {a2}")
        self.destroy()

    def start_drawing(self):
        self.drawing_process = DrawingProcess(self.printer, self.filename.get(), interpolation_resolution=0.1)
        self.drawing_process.start()
        self.monitor_drawing_process()

    def cancel_drawing(self):
        self.drawing_process.stop()
        pass

    def target_xy(self, screen_x, screen_y):
        return (
            linear_map_to(screen_x, 0, self.canvas_width, 0, self.printer.workspace_width),
            linear_map_to(screen_y, 0, self.canvas_height, 0, self.printer.workspace_height)
        )

    def screen_xy(self, printer_x, printer_y):
        return (
            linear_map_to(printer_x, 0, self.printer.workspace_width, 0, self.canvas_width),
            linear_map_to(printer_y, 0, self.printer.workspace_width, 0, self.canvas_height)
        )

    def put_marker(self, x, y, **kwargs):
        r = 2
        return self.canvas.create_oval(x - r, y - r, x + r, y + r, **kwargs)

    def recall_previous_serial_command(self, event):
        self.current_serial_command.set("")
        self.serial_command_entry.insert(0, self.previous_serial_command)

    def send_serial_command(self, event):
        self.previous_serial_command = self.current_serial_command.get()
        self.printer.send_serial_command(self.current_serial_command.get())
        self.current_serial_command.set("")
        pass

    def reset_head(self):
        if self.drawing_process.is_alive():
            return
        self.printer.move_to_alphas(0.0, 0.0)
        pass

    def canvas_click(self, event):
        if self.drawing_process.is_alive():
            return
        self.put_marker(event.x, event.y)
        # self.canvas.create_line(self.curr_xy, event.x, event.y)
        self.printer.move_to_xy(*self.target_xy(event.x, event.y))

    def create_body_frame(self):
        # self.drawing_area_frame = ttk.Frame(self)
        self.canvas = tk.Canvas(self,
                                width=self.canvas_width,
                                height=self.canvas_height, borderwidth=0, highlightthickness=0)
        self.canvas.create_rectangle(0, 0, self.canvas_width - 1, self.canvas_height - 1)

        self.canvas.bind('<ButtonPress-1>', self.canvas_click)
        # self.canvas.bind('<Button1-Motion>', self.canvas_click)
        # ^todo: BUG: button release not detected while printer head moving -> phantom move events. solution: separate thread for printer

        self.canvas.grid(column=0, row=0, sticky=tk.NSEW, padx=10, pady=10)

        # self.drawing_area_frame.grid(column=0, row=0, sticky=tk.NSEW, padx=10, pady=0)

    def create_command_frame(self):
        self.controls_frame = ttk.Frame(self)
        self.controls_frame.grid(column=0, row=1, sticky=tk.EW)

        self.drawing_controls_frame = ttk.LabelFrame(self.controls_frame, text="Drawing process")
        self.drawing_controls_frame.grid(column=0, row=0, sticky=tk.W, padx=10, pady=0)

        self.file_name_entry = ttk.Entry(self.drawing_controls_frame,
                                              textvariable=self.filename,
                                              width=50)
        self.file_name_entry.pack(fill=tk.X)

        self.start_button = ttk.Button(self.drawing_controls_frame, text='Start')
        self.start_button['command'] = self.start_drawing
        self.start_button.pack(fill=tk.BOTH, side=tk.RIGHT)

        self.cancel_button = ttk.Button(self.drawing_controls_frame, text='Cancel')
        self.cancel_button['command'] = self.cancel_drawing
        self.cancel_button.pack(fill=tk.BOTH, side=tk.RIGHT)

        self.printer_controls_frame = ttk.LabelFrame(self.controls_frame, text="Printer Controls")
        self.printer_controls_frame.grid(column=1, row=0, sticky=tk.NW, padx=10, pady=0)


        self.serial_command_entry = ttk.Entry(self.printer_controls_frame,
                                              textvariable=self.current_serial_command,
                                              width=50)
        self.serial_command_entry.pack(fill=tk.X)
        self.serial_command_entry.bind('<Return>', self.send_serial_command, add='+')
        self.serial_command_entry.bind('<Up>', self.recall_previous_serial_command)

        self.reset_button = ttk.Button(self.printer_controls_frame, text='Reset Head')
        self.reset_button['command'] = self.reset_head
        self.reset_button.pack(fill=tk.BOTH, side=tk.RIGHT)

        self.reset_button = ttk.Button(self.printer_controls_frame, text='Zero Angles')
        self.reset_button['command'] = self.printer.zero_position
        self.reset_button.pack(fill=tk.BOTH, side=tk.RIGHT)


    def monitor_drawing_process(self):
        if self.drawing_process.is_alive():
            # check the thread every 100ms
            while not self.drawing_process.drawn_points.empty():
                point = self.drawing_process.drawn_points.get()
                self.canvas.create_line(*self.curr_xy, *self.screen_xy(*point))
                print("line ", self.curr_xy, self.screen_xy(*point))
                self.curr_xy = self.screen_xy(*point)
            self.after(100, lambda: self.monitor_drawing_process())


if __name__ == "__main__":
    app = App()
    app.mainloop()