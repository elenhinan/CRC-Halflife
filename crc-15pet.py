import struct
import serial
import time
import gzip
import os
import random
import win32api
from win32com.shell import shell, shellcon # to get mydocuments path
from threading import Thread
from collections import deque
import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import chisquare

import wx
import wx.animate
from matplotlib import pyplot
from matplotlib.backends.backend_wxagg import FigureCanvasWxAgg as FigureCanvas
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvasAgg

__version__ = filter(str.isdigit, "$Revision: 3 $")

# raw output
# : 46 20 31 38 20 20 05 cc 85 35 40
# : 46 20 31 38 20 20 05 38 98 35 40
# : 46 20 31 38 20 20 05 0f e6 34 40
# csv output
# : F 18  ,4,2,849790,390,047309,22.07.2013,10:21:26
# : F 18  ,4,2,828791,395,045595,22.07.2013,10:21:31
# : F 18  ,4,2,817167,397,568739,22.07.2013,10:21:34

# Half-life
half_life_table = {
	'F 18':109.7,
	'C 11':20.4,
	'N 13':9.96,
	'O 15':2.07,
	'Tc99m':6.0058*60,
	'Cs137':30.17*365*24,
	'TST':5.0
	}
interval = 0.05

# port configuration
portname = 'com1'
baudrate = 4800
timeout = 0.1
retries = 5

# debug
emulate_time = 0

# crc-15 data format
data_size = 11
command = '@ABCDEFGHIJK\r\n'
data_fmt = '<6sbf' # data format: char[6],byte,float
unit_table = ['?','?','uCi','mCi','Ci','MBq','GBq']

# logging
timeformat = '%Y%m%d_%H%M%S'
#log_freq = 2.0 # capture data every n seconds
#log_length = 4#120 # approx log length in half-lifes
record_keys = ['isotope','unit','activity','timestamp']

# radioactive decay
def rad_decay(t, *p):
	a0, t_hl = p
	return a0 * 0.5**(t/t_hl)

class Monitor():
	def __init__(self):
		self.capintec = None
		self.last_time = None
		self.thread = None
		self.thread_sleep = 0.1 # wake up in this interval
		self.interval = 2.
		self.running = False
		self.listeners = []
	
	def addListener(self, listener):
		self.listeners.append(listener)
	
	def start(self):
		self.open_port()
		self.last_time = round(time.time())
		self.running = True
		self.thread = Thread(target=self.timer)
		self.thread.start()
	
	def stop(self):
		self.running = False
		self.last_time = 0.
		self.close_port()
	
	def setInterval(self,freq):
		self.interval = freq
	
	def timer(self):
		while(self.running):
			now = time.time()
			delta = now - self.last_time
			self.check_buffer()
			if delta >= self.interval:
				self.last_time += delta - delta%self.interval
				record = self.read_data()
				if record:
					self.alertListeners(record)
			time.sleep(self.thread_sleep)
		self.thread = None
	
	def check_buffer(self):
		if self.capintec:
			buffer_length = self.capintec.inWaiting()
			if buffer_length > 0: # flush input buffer if not empty
				data = self.capintec.read(buffer_length)
				if '\n' in data:
					print 'Print pressed, data: %s'%data
				
	def alertListeners(self,earg=None):
		for listener in self.listeners:
			Thread(target=listener,args=[earg]).start()
		
	def open_port(self):
		try:
			self.capintec = serial.Serial(portname,baudrate,timeout=timeout)
		except:
			print 'Failed to open port %s, reverting to emulation mode'%portname
			return
		#test if data can be read
		if not self.read_data():
			print 'Failed to open port %s, reverting to emulation mode'%portname
			self.close_port()
	
	def close_port(self):
		if self.capintec:
			self.capintec.close()
			self.capintec = None

	def read_data(self):
		if not self.capintec:
			global emulate_time
			timestamp = round(time.time()+0.5) # seconds since 1970 or whatever
			if emulate_time == 0:
				emulate_time = timestamp
			act = rad_decay(timestamp-emulate_time,*(2.,half_life_table['TST']*60.))
			act *= random.gauss(1.,0.025)
			record_values = 'TST', 'GBq', act, timestamp # format data
			new_record = dict(zip(record_keys,record_values)) # create dictionary
			return new_record

		for i in range(retries):
			self.capintec.flushInput() # flush buffer to be sure of no desync
			self.capintec.write(command)
			data = self.capintec.read(data_size)
			timestamp = round(time.time()+0.5) # seconds since 1970 or whatever
			if len(data) == data_size:
				break
		else:
			print 'Failed after %d retries'%retries # return and do nothing if failed
			return None
	
		# parse data and add to log if successfull
		isotope, unit, activity = struct.unpack(data_fmt,data) # unpack/parse data from crc15
		isotope = isotope.strip()

		if not ((unit < len(unit_table)) and (isotope in half_life_table)):
			print 'Data error: unit [%s],isotope [%s]'%(unit,isotope)
			return None
		record_values = isotope, unit_table[unit], activity, timestamp # format data
		new_record = dict(zip(record_keys,record_values)) # create dictionary
		return new_record
	
	def __del__(self):
		if self.running:
			self.stop()
	
class MainWindow(wx.Frame):
	def __init__(self, parent, title):
		wx.Frame.__init__(self, parent, title=title, size=(1000,600))

		# set default values
		self.user = ''
		self.batch = ''
		self.fitFrom = None
		self.halflife = None
		self.logstart = None
		self.logfile = None
		self.logging = True
		self.act = []
		self.actMin = 0.001
		self.actMax = 10.
		self.need_reset = None
		self.autoexport = '1/4'
		self.autoexport_value = 0.25
		self.autoexport_steps = ['Off','1/16','1/8','1/4','1/2','1','2','4','8','16']
		self.logplot = False
		self.logfreq = 10.
		self.logfreq_steps = [2.,5.,10.,30.,60.,120.,300.,600.]
		self.loglength = 30.
		self.loglength_steps = [5.,10.,15.,30.,60.,120.,300.,600,24*60.]
		self.image_animation_filename = 'poptartcat2plz.gif'
		self.image_inactive_filename = 'poptartcat2plz_gray.gif'		
		self.foldername = 'crc-15pet'
		
		self.setupDirectory()
		
		# create monitor
		self.monitor = Monitor()
		self.monitor.setInterval(self.logfreq)
		self.monitor.addListener(self.onRecord)
		
		# data
		self.unit = None
		self.isotope = None
		self.halflife = None
		
		# main window layout, two horizontal panels
		self.sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.panel = wx.Panel(self)
		self.panel.SetSizer(self.sizer)
		
		self.createControls()
		
		# create figure
		self.plotsizer = wx.BoxSizer(wx.VERTICAL)
		self.sizer.Add(self.plotsizer,1,wx.EXPAND)
		self.createFigure()	
		self.plotsizer.Add(self.figcanvas,1,wx.EXPAND)
								
		#show frame
		self.Show(True)

	def setupDirectory(self):
		self.savepath = os.path.join(shell.SHGetFolderPath(0,shellcon.CSIDL_PERSONAL,None,0),self.foldername)
		if not os.path.exists(self.savepath):
			os.makedirs(self.savepath)
		
	def createControls(self):
		# create controls
		self.controlsizer = wx.BoxSizer(wx.VERTICAL)
		self.sizer.Add(self.controlsizer,0,wx.EXPAND|wx.ALL,border=10)
		
		# create widgets
		self.button_monitor = wx.ToggleButton(self.panel,-1,u'Start/Stop')
		self.button_monitor.Bind(wx.EVT_TOGGLEBUTTON,self.onStartStop)
		
		self.button_export = wx.Button(self.panel,-1,u'Export')
		self.button_export.Bind(wx.EVT_BUTTON,self.onExport)
		
		self.text_logfreq = wx.StaticText(self.panel,-1,u'Interval: %4.1f (s)'%self.logfreq)
		self.slider_logfreq = wx.Slider(self.panel,-1,self.logfreq_steps.index(self.logfreq),0,len(self.logfreq_steps)-1)
		self.slider_logfreq.Bind(wx.EVT_SCROLL, self.onLogFreq)
		
		self.text_loglength = wx.StaticText(self.panel,-1,u'Window: %3.1f (min)'%self.loglength)
		self.slider_loglength = wx.Slider(self.panel,-1,self.loglength_steps.index(self.loglength),0,len(self.loglength_steps)-1)
		self.slider_loglength.Bind(wx.EVT_SCROLL, self.onLogLength)

		self.checkbox_logging = wx.CheckBox(self.panel,-1,u'Log to file')
		self.checkbox_logging.SetValue(self.logging)
		self.checkbox_logging.Bind(wx.EVT_CHECKBOX, self.onChangeLogging)
		self.checkbox_logging.Enabled = False
		
		self.checkbox_logplot = wx.CheckBox(self.panel,-1,u'Logarithmic Y')
		self.checkbox_logplot.SetValue(self.logplot)
		self.checkbox_logplot.Bind(wx.EVT_CHECKBOX, self.onLogPlot)
		
		self.text_autoexport = wx.StaticText(self.panel,-1,u'Autoexport: %s (t\u00bd)'%self.autoexport)
		self.slider_autoexport = wx.Slider(self.panel,-1,self.autoexport_steps.index(self.autoexport),0,len(self.autoexport_steps)-1)
		self.slider_autoexport.Bind(wx.EVT_SCROLL, self.onChangeAutoExport)
		
		self.button_about = wx.Button(self.panel,-1,u'About')
		self.button_about.Bind(wx.EVT_BUTTON,self.onAbout)
		
		self.image_running = wx.animate.GIFAnimationCtrl(self.panel,-1,self.image_animation_filename)
		self.image_running.SetInactiveBitmap(wx.Bitmap(self.image_inactive_filename))
		
		# layout
		self.controlsizer.Add(self.button_monitor,flag=wx.EXPAND)
		self.controlsizer.AddSpacer(5)
		self.controlsizer.Add(self.image_running,flag=wx.ALIGN_CENTER)
		self.controlsizer.AddSpacer(5)
		self.controlsizer.Add(self.checkbox_logging)
		self.controlsizer.AddSpacer(10)
		self.controlsizer.Add(self.checkbox_logplot)
		self.controlsizer.AddSpacer(10)
		self.controlsizer.Add(self.text_logfreq)
		self.controlsizer.Add(self.slider_logfreq)
		self.controlsizer.Add(self.text_loglength)
		self.controlsizer.Add(self.slider_loglength)
		self.controlsizer.AddSpacer(10)
		self.controlsizer.Add(self.text_autoexport)
		self.controlsizer.Add(self.slider_autoexport)
		self.controlsizer.AddSpacer(10)
		self.controlsizer.Add(self.button_export,flag=wx.EXPAND)
		self.controlsizer.AddStretchSpacer()
		self.controlsizer.Add(self.button_about,flag=wx.EXPAND)
		self.controlsizer.AddSpacer(10)
		
		# create user and batch dialogs
		self.userdialog = wx.TextEntryDialog(self, u'Enter four letter username', u'User', style=wx.OK|wx.CENTRE)
		self.batchdialog = wx.TextEntryDialog(self, u'Enter batch number', u'Batch#', style=wx.OK|wx.CENTRE)
		
	def getSessionData(self):
		if self.userdialog.ShowModal() == wx.ID_OK:
			self.user = self.userdialog.GetValue()
		if self.batchdialog.ShowModal() == wx.ID_OK:
			self.batch = self.batchdialog.GetValue()
	
	def createFigure(self):
		#set up figure canvas
		self.figure = pyplot.Figure()
		self.figcanvas = FigureCanvas(self.panel,-1,self.figure)
		self.figcanvas.mpl_connect('button_press_event', self.onPlotClick)
		#set up axes
		rect = [0.10,0.10,0.85,0.85]
		self.ax = self.figure.add_axes(rect)		
		
	def clear_fig(self,ax):
		ax.cla()
		ax.set_title('User: %s | Batch#: %s | Time: %s'%(self.user,self.batch,time.strftime('%d/%m-%Y %H:%M:%S')))
		ax.set_ylim((self.actMin,self.actMax))
		ax.set_xlim((-self.loglength,0))
		ax.set_xlabel('Time (min)')
		ax.set_ylabel('Activity (%s)'%self.unit)
		if self.logplot:
			ax.set_yscale('log')
		else:
			ax.set_yscale('linear')
		ax.grid(which='both')
	
	def updateplot(self):
		self.plotdata(self.ax)
		self.figcanvas.draw()
	
	def plotdata(self,ax):
		if len(self.act) < 2:
			return
			
		n = int(min(len(self.act),round((self.loglength*60./self.logfreq)+1.0)))
		a = np.array(self.act)[-n:]
		t = np.array(self.ts)[-n:]
		t0 = t[-1]
		t = (t-t0)/60.

		r = 0.2
		min_value = np.min(a)
		max_value = np.max(a)
		min_exp = 10**(np.floor(np.log10(min_value)))
		max_exp = 10**(np.floor(np.log10(max_value)))
		if self.logplot:
			self.actMin = np.floor(min_value/(min_exp*r))*r*min_exp
		else:
			self.actMin = np.floor(min_value/(max_exp*r))*r*max_exp
		self.actMax = np.ceil(max_value/(max_exp*r))*r*max_exp
		
		self.clear_fig(ax)
		ax.plot(t,a,'r.')

		if a.shape[0] > 3:
			# fit from beginning of data in window, or from selected point
			self.fitFrom = max(self.fitFrom,self.ts[-n])
			
			t0_text = time.strftime('%H:%M:%S',time.localtime(self.fitFrom))
		
			t0_win = (self.fitFrom-t0)/60.
			# plot line at start of fitting window
			i0_win = np.searchsorted(t,t0_win,'left')
			t_win = t[i0_win:] - t0_win
			a_win = a[i0_win:]
			# initial guess for fitting and run fit
			p0 = (a_win[0],self.halflife)
			coeff, cov = curve_fit(rad_decay,t_win,a_win,sigma=None,p0=p0)
			
			a_fit = rad_decay(t_win,*coeff)
			a_win_log = np.log(a_win)
			a_fit_log = np.log(a_fit)
			r = 1. - np.sum((a_win_log-a_fit_log)**2.)/np.sum((a_win_log-np.mean(a_win_log))**2.)
			
			# create fitted curve
			t_new_delta = self.logfreq/60./10.
			t_new = np.arange(0.,t_win[-1]+t_new_delta,t_new_delta)
			a_new = rad_decay(t_new,*coeff)
			
			#extract coefficents and uncertanties
			fit_a0, fit_hl = coeff
			fit_a0_u = cov[0,0]
			fit_hl_u = cov[1,1]
			ax.plot(t_new+t0_win,a_new,'b-')
			ax.vlines(t0_win, self.actMin, self.actMax,color='b',linestyles='dashed')
			fit_text = u'Data fit (%s):\nt0: %s\nA0: %4.3f \u00B1 %4.3f (%s)\nt\u00BD: %4.1f \u00B1 %4.1f (min)\nR: %4.3f\nn: %d'%(self.isotope,t0_text,fit_a0,fit_a0_u*3,self.unit,fit_hl,fit_hl_u*3,r,len(a_win))
			
			# set color depending if result is inside accepted interval
			if self.halflife * (1.-interval) <= fit_hl-3*fit_hl_u  and fit_hl+3*fit_hl_u <= self.halflife * (1.+interval):
				fit_color = 'g'
			else:
				fit_color = 'r'
			
			ax.text(0.04,0.07,fit_text,color=fit_color,family='monospace',fontsize=12,transform = ax.transAxes)
		
		if ax == self.ax:
			ax.text(0.5,0.85,'%3.2f %s'%(a[-1],self.unit),color='black',alpha=.1,family='monospace',fontsize=72,transform = ax.transAxes, ha='center',va='center')
		ax.text(0.5,0.5,self.isotope.replace(' ',''),color='black',alpha=.1,family='monospace',fontsize=144,transform = ax.transAxes, ha='center',va='center')
	
	def onPlotClick(self, event):
		self.fitFrom = event.xdata*60.+self.ts[-1]
		self.updateplot()
#		if debug:
#			print 'button=%d,x=%d,y=%d,xdata=%f,ydata=%f'%(event.button,event.x,event.y,event.xdata,event.ydata)
	
	def reset(self):
		self.halflife = half_life_table[self.isotope]
		buffer_len = np.max(self.loglength_steps)*60./np.min(self.logfreq_steps) #int(self.halflife*60./self.logfreq+1.0)
		
		print 'Creating buffers for %s with size=%d, half life=%4.1f\n'%(self.isotope,buffer_len,self.halflife)
		
		self.act = deque(maxlen=buffer_len) # create list of records, with log length as specified above
		self.ts =  deque(maxlen=buffer_len) # create list of records, with log length as specified above

		self.fitFrom = None
		
		self.need_reset = False
	
	def onLogFreq(self,event):
		index = self.slider_logfreq.GetValue()
		self.logfreq = self.logfreq_steps[index]
		self.monitor.setInterval(self.logfreq)
		self.text_logfreq.SetLabel('Interval: %4.1f (s)'%self.logfreq)
	
	def onLogLength(self,event):
		index = self.slider_loglength.GetValue()
		self.loglength = self.loglength_steps[index]
		self.text_loglength.SetLabel(u'Window: %3.1f (min)'%self.loglength)
		self.updateplot()
	
	def onLogPlot(self,event):
		self.logplot = event.Checked()
		self.updateplot()
	
	def onChangeLogging(self, event):
		self.logging = event.Checked()
	
	def onChangeAutoExport(self, event):
		index = self.slider_autoexport.GetValue()
		self.autoexport = self.autoexport_steps[index]
		self.text_autoexport.SetLabel(u'Autoexport: %s (t\u00bd)'%self.autoexport)
		if self.autoexport == 'Off':
			self.autoexport_value = None
		else:
			self.autoexport_value = eval(self.autoexport+'.')
		
	def onStartStop(self, event=None):
		if self.monitor.running:
			self.Stop()
		else:
			self.Start()
		
	def onExport(self, event):
		self.Export()
	
	def Stop(self):
		if self.monitor.running:
			self.monitor.stop()
			if self.logfile:
				self.logfile.close()
				self.logfile = None
			self.image_running.Stop()
			self.button_monitor.SetValue(self.monitor.running)
	
	def Start(self):
		filename='test'
		if not self.monitor.running:
			# clear logs and start monitoring activity
			self.getSessionData() # get username and batch number
			self.need_reset = True
			self.monitor.start()
			self.image_running.Play()
			self.button_monitor.SetValue(self.monitor.running)
	
	def Export(self, dialog=True):
		filename = '%s-%s-%s.pdf'%(self.batch,self.user,time.strftime(timeformat))
		filepath = os.path.join(self.savepath,filename)
		exp_fig = pyplot.Figure(figsize=(8,6))
		exp_canvas = FigureCanvasAgg(exp_fig)
		rect = [0.10,0.26,0.85,0.70]
		exp_ax = exp_fig.add_axes(rect)		
		self.plotdata(exp_ax)
		
		#'Analyse  dato/sign'
		#'Kontroll dato/sign'
		exp_text = u'Analyse  dato/sign:  ../..-....   ....................\n\n\nKontroll dato/sign:  ../..-....   ....................'.replace('.',u'\u2026')
		exp_fig.text(0.05,0.03,exp_text,family='monospace',fontsize=12,transform=exp_fig.transFigure)
		exp_fig.savefig(filepath)
		
		if dialog:
			dlg = wx.MessageDialog(self,u'Plot exported to %s\nPrint output?'%filename,'Export complete', wx.YES|wx.NO|wx.ICON_INFORMATION)
			if dlg.ShowModal() == wx.ID_YES:
				win32api.ShellExecute(0,'print',filepath,None,'.',0)
		else:
			dlg = wx.MessageDialog(self,u'%s t\u00bd passed\nPlot exported to %s\nProcess stopped, now printing...'%(self.autoexport,filename),'Auto Export', wx.OK|wx.ICON_INFORMATION)
			dlg.ShowModal()
			win32api.ShellExecute(0,'print',filepath,None,'.',0)
	
	def onRecord(self, event):
		if self.unit != event['unit']:
			self.need_reset = True
		self.unit = event['unit']
		
		if self.isotope != event['isotope']:
			self.need_reset = True
		self.isotope = event['isotope']
		
		if self.need_reset:
			self.reset()
			
		self.act.append(event['activity'])
		self.ts.append(event['timestamp'])
		self.writeLog(event)
		self.updateplot()
		
		#check if autoexport time has passed
		if self.autoexport_value>0:
			if (self.autoexport_value*self.halflife*60) < self.ts[-1]-self.ts[0]:
				self.Stop()
				self.Export(False) # print without asking

	def newLogfile(self):
		self.logstart = time.localtime()
		filename = '%s-%s-%s.log.gz'%(self.batch,self.user,time.strftime(timeformat,self.logstart))
		filepath = os.path.join(self.savepath,filename)
		self.logfile = gzip.GzipFile(filepath,'w') # open logfile for writing
		
	def writeLog(self,event):
		if self.logging:
			if not self.logfile:
				self.newLogfile()
			# if new day, start new logfile
			if self.logstart.tm_yday != time.localtime().tm_yday:
				self.logfile.close()
				self.newLogfile()

			self.logfile.write(str(event)+'\n') # write dictionary to file
		else:
			if self.logfile:
				self.logfile.close()
				self.logfile = None
	
	def onAbout(self, event):
		dlg = wx.MessageDialog(self, "Capintec CRC15-PET logger\nNjal Brekke 2013\nnjal.brekke@helse-bergen.no\nRevision: %s"%__version__, "About CRC15-PET logger", wx.OK)
		dlg.ShowModal() # Show it
		dlg.Destroy() # finally destroy it when finished.
		
	def __del__(self):
		self.monitor.stop()
		if self.logfile:
			self.logfile.close()
		
if __name__ == "__main__":
	#main()
	app = wx.App(False)
	frame = MainWindow(None,'CRC15-PET logger')
	app.MainLoop()