#######################################################)
# File: ShiftMonitorTool.py
# Author: Charlie Mueller
# Date Created: May 4, 2016
#
# Dependencies: DBParser.py, mailAlert.py
#
# Data Type Key:
#    { a, b, c, ... }    -- denotes a tuple
#    [ key ] <object>  -- denotes a dictionary of keys associated with objects
#    ( object )          -- denotes a list of objects
#######################################################

#Issues: Prints "No useable triggers" when normally shouldn't
#Issues: Records more total triggers then old code (777 vs 728 for Run 276794)
#Issues: Displays same trouble getting deadtime, but less often --> problem?
#Issues: Can't find triggers from fitfile --> seems to be because those triggers arent recording yet, problem?
#Issues: Crashed when LS stopped updating
#    Traceback (most recent call last):
#      File "ShiftMonitorTool.py", line 254, in <module>
#        parser.run()
#      File "ShiftMonitorTool.py", line 157, in run
#        self.monitor.run()
#      File "/afs/cern.ch/work/a/awightma/RateMon/ShiftMonitorNCR_dev.py", line 233, in run
#        self.checkTriggers_dev(avgLumi,trigger_table)
#      File "/afs/cern.ch/work/a/awightma/RateMon/ShiftMonitorNCR_dev.py", line 543, in checkTriggers_dev
#        if self.badRates.has_key(trigger): del self.badRates
#    AttributeError: ShiftMonitor instance has no attribute 'badRates'

# Imports
from DBParser import *
import ROOT
from ROOT import TF1
import cPickle as pickle
import sys
import time
# For colors
from termcolor import *
from colors import *
# For getting command line options
import getopt
# For mail alerts
from mailAlert import mailAlert, audioAlert
from Logger import *

# --- 13 TeV constant values ---
ppInelXsec = 80000.
orbitsPerSec = 11246.

# Use: Writes a string in a fixed length margin string (pads with spaces)
def stringSegment(strng, tot):
    string = str(strng)
    for x in range(0, tot-len(str(strng))):
        string += " "
    return string

# Alias for sys.stdout.write
sys.stdout = Logger()
write = sys.stdout.write

# Class ShiftMonitor
class ShiftMonitor:

    def __init__(self):
        # Suppress root warnings
        ROOT.gErrorIgnoreLevel = 7000
        # Fits and fit files
        self.fitFile = "Fits/2016/FOG.pkl"               # The fit file, can contain both HLT and L1 triggers
        self.InputFitHLT = None         # The fit information for the HLT triggers
        self.InputFitL1 = None          # The fit information for the L1 triggers
        # DBParser
        self.parser = DBParser()        # A database parser
        # Rates
        self.HLTRates = None            # HLT rates
        self.L1Rates = None             # L1 rates
        self.Rates = None               # Combined L1 and HLT rates
        self.deadTimeData = {}          # initializing deadTime dict
        # Run control
        self.runNumber = -1             # The number of the current run
        self.numBunches = [-1, -1]     # Number of [target, colliding] bunches
        # Lumisection control
        self.lastLS = 1                 # The last LS that was processed last segment
        self.currentLS = 1              # The latest LS written to the DB
        self.slidingLS = -1             # The number of LS to average over, use -1 for no sliding LS
        self.useLSRange = False         # Only look at LS in a certain range
        # Mode
        self.triggerMode = None         # The trigger mode
        self.mode = None                # Mode: cosmics, circulate, physics
        # Columns header
        self.displayRawRates = False    # display raw rates, to display prescaled rates, set = True
        self.pileUp = True              # derive expected rate as a function of the pileUp, and not the luminosity
        # Triggers
        self.cosmics_triggerList = "monitorlist_COSMICS.list" #default list used when in cosmics mode
        self.collisions_triggerList = "monitorlist_COLLISIONS.list" #default list used when in collision mode 
        self.triggerList = ""           # A list of all the L1 and HLT triggers we want to monitor
        self.userSpecTrigList = False   # User specified trigger list 
        self.usableHLTTriggers = []     # HLT Triggers active during the run that we have fits for (and are in the HLT trigger list if it exists)
        self.otherHLTTriggers = []      # HLT Triggers active during the run that are not usable triggers
        self.usableL1Triggers = []      # L1 Triggers active during the run that have fits for (and are in the L1 trigger list if it exists)
        self.otherL1Triggers = []       # L1 Triggers active during that run that are not usable triggers
        self.redoTList = True           # Whether we need to update the trigger lists
        self.useAll = False             # If true, we will print out the rates for all the HLT triggers
        self.useL1 = False              # If true, we will print out the rates for all the L1 triggers
        self.totalHLTTriggers = 0       # The total number of HLT Triggers on the menu this run
        self.totalL1Triggers = 0        # The total number of L1 Triggers on the menu this run
        self.fullL1HLTMenu = []
        self.ignoreStrings = ["Calibration","L1Tech"]
        # Restrictions
        self.removeZeros = False        # If true, we don't show triggers that have zero rate
        self.requireLumi = False        # If true, we only display tables when aveLumi is not None
        # Trigger behavior
        self.percAccept = 50.0          # The acceptence for % diff
        self.devAccept = 5              # The acceptance for deviation
        self.badRates = {}              # A dictionary: [ trigger name ] { num consecutive bad , whether the trigger was bad last time we checked, rate, expected, dev }
        self.recordAllBadTriggers = {}  # A dictionary: [ trigger name ] < total times the trigger was bad >
        self.maxCBR = 3                 # The maximum consecutive db queries a trigger is allowed to deviate from prediction by specified amount before it's printed out
        self.displayBadRates = -1       # The number of bad rates we should show in the summary. We use -1 for all
        self.usePerDiff = False         # Whether we should identify bad triggers by perc diff or deviatoin
        self.sortRates = True           # Whether we should sort triggers by their rates
        self.maxHLTRate = 500           # The maximum prescaled rate we allow an HLT Trigger to have
        self.maxL1Rate = 30000          # The maximum prescaled rate we allow an L1 Trigger to have
        # Other options
        self.quiet = False              # Prints fewer messages in this mode
        self.noColors = False           # Special formatting for if we want to dump the table to a file
        self.sendMailAlerts_static = True      # Whether we should send alert mails
        self.sendMailAlerts_dynamic = self.sendMailAlerts_static 
        self.sendAudioAlerts = False     # Whether we should send audio warning messages in the control room (CAUTION)
        self.isUpdating = True          # flag to determine whether or not we're receiving new LS
        self.showStreams = False        # Whether we should print stream information
        self.showPDs = False            # Whether we should print pd information
        self.totalStreams = 0           # The total number of streams
        self.maxStreamRate = 1000000    # The maximum rate we allow a "good" stream to have
        self.maxPDRate = 10000000       # The maximum rate we allow a "good" pd to have        
        self.lumi_ave = "NONE"
        self.pu_ave = "NONE"
        self.deadTimeCorrection = True  # correct the rates for dead time
        self.scale_sleeptime = 1.0      # Scales the length of time to wait before sending another query (1.0 = 60sec, 2.0 = 120sec, etc)


        self.badTriggers = {}       #[trigger name] <List of trigger infos>

    # Use: Opens a file containing a list of trigger names and adds them to the RateMonitor class's trigger list
    # Note: We do not clear the trigger list, this way we could add triggers from multiple files to the trigger list
    # -- fileName: The name of the file that trigger names are contained in
    # Returns: (void)
    def loadTriggersFromFile(self, fileName):
        try:
            file = open(fileName, 'r')
        except:
            print "File", fileName, "(a trigger list file) failed to open."
            return
        
        allTriggerNames = file.read().split() # Get all the words, no argument -> split on any whitespace
        TriggerList = []
        for triggerName in allTriggerNames:
            # Recognize comments
            if triggerName[0]=='#': continue
            try:
                if not str(triggerName) in TriggerList:
                    TriggerList.append(stripVersion(str(triggerName)))
            except:
                print "Error parsing trigger name in file", fileName
        return TriggerList

    # Use: Formats the header string
    def getHeader(self):
        # Define spacing and header
        maxNameHLT = 0
        maxNameL1 = 0
        if len(self.usableHLTTriggers)>0 or len(self.otherHLTTriggers)>0:
            maxNameHLT = max([len(trigger) for trigger in self.usableHLTTriggers+self.otherHLTTriggers])
        if len(self.usableL1Triggers)>0 or len(self.otherL1Triggers)>0:
            maxNameL1 = max([len(trigger) for trigger in self.usableL1Triggers+self.otherL1Triggers])

        # Make the name spacing at least 90
        maxName = max([maxNameHLT, maxNameL1, 90])
        
        self.spacing = [maxName + 5, 14, 14, 14, 14, 14, 0]
        self.spacing[5] = max( [ 181 - sum(self.spacing), 0 ] )

        self.header = ""                # The table header
        self.header += stringSegment("* TRIGGER NAME", self.spacing[0])
        if (self.displayRawRates): self.header += stringSegment("* RAW [Hz]", self.spacing[1])
        else: self.header += stringSegment("* ACTUAL [Hz]", self.spacing[1])
        self.header += stringSegment("* EXPECTED", self.spacing[2])
        self.header += stringSegment("* % DIFF", self.spacing[3])
        self.header += stringSegment("* DEVIATION", self.spacing[4])
        self.header += stringSegment("* AVE PS", self.spacing[5])
        self.header += stringSegment("* COMMENTS", self.spacing[6])
        self.hlength = 181 #sum(self.spacing)
        
    # Use: Runs the program
    # Returns: (void)
    def run(self):
        # Load the fit and trigger list
        if self.fitFile != "":
            inputFit = self.loadFit(self.fitFile)
            for triggerName in inputFit:
                if triggerName[0:3] == "L1_":
                    if self.InputFitL1 is None: self.InputFitL1 = {}
                    self.InputFitL1[triggerName] = inputFit[triggerName]
                elif triggerName[0:4] == "HLT_":
                    if self.InputFitHLT is None: self.InputFitHLT = {}
                    self.InputFitHLT[triggerName] = inputFit[triggerName]
        
        # Sort trigger list into HLT and L1 trigger lists
        if self.triggerList != "":
            for triggerName in self.triggerList:
                if triggerName[0:3] == "L1_":
                    self.TriggerListL1.append(triggerName)
                else:
                    self.TriggerListHLT.append(triggerName)
        # If there aren't preset trigger lists, use all the triggers that we can fit
        else:
            if not self.InputFitHLT is None: self.TriggerListHLT = self.InputFitHLT.keys()
            if not self.InputFitL1 is None: self.TriggerListL1 = self.InputFitL1.keys()

        while True:
            try:
                previous_run_number = self.runNumber
                
                #self.updateRunInfo(previous_run_number)
                self.runNumber, _, _, mode = self.parser.getLatestRunInfo()
                self.triggerMode = mode[0]

## ---Run loop start---

                is_new_run = ( previous_run_number != self.runNumber or self.runNumber < 0 ) 
                if is_new_run:
                    print "Starting a new run: Run %s" % (self.runNumber)
                    self.lastLS = 1
                    self.currentLS = 0
                    self.setMode()
                    physicsActive,avgLumi,avgDeadTime,avgL1rate,PScol = self.queryDB()
                    self.redoTriggerLists()
                else:
                    physicsActive,avgLumi,avgDeadTime,avgL1rate,PScol = self.queryDB()

                self.lumi_ave = avgLumi

                #Construct (or reconstruct) trigger lists
                if self.redoTList:
                    self.redoTriggerLists()

                if len(self.HLTRates) == 0 or len(self.L1Rates) == 0:
                    self.redoTList = True

                # Make sure there is info to use        
                if len(self.HLTRates) == 0 and len(self.L1Rates) == 0:
                    print "No new information can be retrieved. Waiting... (There may be no new LS, or run active may be false)"
                    continue

                trigger_table = self.getTriggerTable(physicsActive,avgLumi)

                self.checkTriggers(avgLumi,trigger_table)

                # If there are lumisection to show, print info for them
                if self.currentLS > self.lastLS:
                    self.printTable(trigger_table,physicsActive,avgLumi,avgDeadTime,avgL1rate,PScol)
                    self.isUpdating = True
                else:
                    self.isUpdating = False
                    print "Not enough lumisections. Last LS was %s, current LS is %s. Waiting." % (self.lastLS, self.currentLS)

## ---Run loop start---

                self.mailSender()
                self.sleepWait()

            except KeyboardInterrupt:
                print "Quitting. Bye."
                break
    
    # Queries the Database and updates trigger entries
    def queryDB(self):
        # Get Rates: [triggerName][LS] { raw rate, prescale }
        if not self.useLSRange:
            self.HLTRates = self.parser.getRawRates(self.runNumber, self.lastLS)
            self.L1Rates = self.parser.getL1RawRates(self.runNumber, self.lastLS)
            self.streamData = self.parser.getStreamData(self.runNumber, self.lastLS)
            self.pdData = self.parser.getPrimaryDatasets(self.runNumber, self.lastLS)
        else:
            self.HLTRates = self.parser.getRawRates(self.runNumber, self.LSRange[0], self.LSRange[1])
            self.L1Rates = self.parser.getL1RawRates(self.runNumber, self.LSRange[0], self.LSRange[1])
            self.streamData = self.parser.getStreamData(self.runNumber, self.LSRange[0], self.LSRange[1])
            self.pdData = self.parser.getPrimaryDatasets(self.runNumber, self.LSRange[0], self.LSRange[1])
        self.totalStreams = len(self.streamData.keys())
        self.Rates = {}
        self.Rates.update(self.HLTRates)
        self.Rates.update(self.L1Rates)
        self.totalHLTTriggers = len(self.HLTRates.keys())
        self.totalL1Triggers = len(self.L1Rates.keys())


        lslist = []
        for trig in self.Rates.keys():
            if len(self.Rates[trig]) > 0: lslist.append(max(self.Rates[trig]))
        # Update lastLS
        self.lastLS = self.currentLS
        # Update current LS
        if len(lslist) > 0: self.currentLS = max(lslist)

        if self.useLSRange: # Adjust runs so we only look at those in our range
            self.slidingLS = -1 # No sliding LS window
            self.lastLS = max( [self.lastLS, self.LSRange[0]] )
            self.currentLS = min( [self.currentLS, self.LSRange[1] ] )

        # Get the inst lumi
        avgDeadTime = 0
        try:
            self.deadTimeData = self.parser.getDeadTime(self.runNumber)
            avgDeadTime = 0
        except:
            self.deadTimeData = {}
            avgDeadTime = None
            print "Error getting deadtime data"
        # Get total L1 rate
        l1rate = 0
        try:
            l1rateData = self.parser.getL1rate(self.runNumber)
            avgL1rate = 0
        except:
            l1rateData = {}
            avgL1rate = None
            print "Error getting total L1 rate data"


        self.startLS = self.lastLS
        physicsActive = False
        avgLumi = 0
        PScol = -1
        if self.mode != "cosmics":
            lumiData = self.parser.getLumiInfo(self.runNumber, self.startLS, self.currentLS)
            self.numBunches = self.parser.getNumberCollidingBunches(self.runNumber)
            # Find the average lumi since we last checked
            count = 0
            # Get luminosity (only for non-cosmic runs)
            #for LS, instLumi, psi, physics in lumiData:
            for LS, instLumi, psi, physics, all_subSys_good in lumiData:
                # If we are watching a certain range, throw out other LS
                if self.useLSRange and (LS < self.LSRange[0] or LS > self.LSRange[1]): continue

                # Average our instLumi
                if not instLumi is None and physics:
                    physicsActive = True
                    PScol = psi
                    if not avgDeadTime is None and self.deadTimeData.has_key(LS): 
                        avgDeadTime += self.deadTimeData[LS]
                    else: 
                        avgDeadTime = 0
                    if not avgL1rate is None and l1rateData.has_key(LS):
                        avgL1rate += l1rateData[LS]
                    else:
                        avgL1rate = 0
                    avgLumi += instLumi
                    count += 1
            if count == 0:
                avgLumi = None
            else:
                avgLumi /= float(count)
                avgDeadTime /= float(count)
                avgL1rate /= float(count)
        else:
            count = 0
            avgLumi = None
            for LS in l1rateData.keys():
                if not avgDeadTime is None and self.deadTimeData.has_key(LS):
                    avgDeadTime += self.deadTimeData[LS]
                else:
                    avgDeadTime = 0

                if not avgL1rate is None and l1rateData.has_key(LS):
                    avgL1rate += l1rateData[LS]
                else:
                    avgL1rate = 0
            if not count == 0:
                avgDeadTime /= float(count)
                avgL1rate /= float(count)

        if self.numBunches[0] > 0 and not avgLumi == None:
            self.pu_ave = avgLumi/self.numBunches[0]*ppInelXsec/orbitsPerSec
        else:
            self.pu_ave = "NONE"

        return physicsActive,avgLumi,avgDeadTime,avgL1rate,PScol

    def getTriggerTable(self, physicsActive, avgLumi):
        # A list of tuples, each a row in the table: 
        #   ( { trigger, rate, predicted rate, sign of % diff, abs % diff, sign of sigma, abs sigma,
        #       ave PS, doPred, comment } )
        table = {}

        #Calculate trigger information
        #for trigger in self.triggerList:
        tmp_trigger_list = []
        tmp_trigger_list += self.HLTRates.keys()
        tmp_trigger_list += self.L1Rates.keys()
        #for trigger in self.fullL1HLTMenu:
        for trigger in tmp_trigger_list:
        #for trigger in self.Rates.keys():
            perc = 0
            dev = 0
            aveRate = 0
            properAvePSRate = 0
            avePS = 0
            avePSExpected = 0
            count = 0
            comment = ""
            doPred = physicsActive and self.mode == "collisions"

            if not self.Rates.has_key(trigger): continue

            # If cosmics, don't do predictions
            if self.mode == "cosmics": doPred = False

            # Calculate rate
            if self.mode != "cosmics" and doPred:
                if not avgLumi is None:
                    if trigger in self.TriggerListL1:
                        self.L1 = True
                    else:
                        self.L1 = False
                    expected = self.calculateRate(trigger, avgLumi)
                    if expected < 0: expected = 0                # Don't let expected value be negative
                    avePSExpected = expected
                    # Get the mean square error (standard deviation)
                    mse = self.getMSE(trigger)
                else:
                    expected = None
                    avePSExpected = None
                    mse = None

            correct_for_deadtime = self.deadTimeCorrection
            if trigger[0:3]=="L1_": correct_for_deadtime = False

            for LS in self.Rates[trigger].keys():
                if LS < self.startLS or LS > self.currentLS: continue

                prescale = self.Rates[trigger][LS][1]
                rate = self.Rates[trigger][LS][0]

                try:
                    deadTime = self.deadTimeData[LS]
                except:
                    print "trouble getting deadtime for LS: ", LS," setting DT to zero"
                    deadTime = 0.0

                if correct_for_deadtime: rate *= 1. + (deadTime/100.)
                    
                if prescale > 0: 
                    properAvePSRate += rate/prescale
                else:
                    properAvePSRate += rate

                aveRate += rate
                count += 1
                avePS += prescale

            if count > 0:
                if aveRate == 0: comment += "0 counts "
                aveRate /= count
                properAvePSRate /= count
                avePS /= count
            else:
                #comment += "PS=0"
                comment += "No rate yet "
                doPred = False

            if doPred and not avePSExpected is None and avePS > 1: 
                avePSExpected /= avePS

            # skips if we are not making predictions for this trigger and we are throwing zeros
            #if not doPred and self.removeZeros and aveRate == 0: 
            #    continue

            if self.displayRawRates:
                rate_val = aveRate
            else:
                rate_val = properAvePSRate

            if doPred and not expected is None: 
                #Do nothing
                xkcd = ""
            else: 
                avePSExpected = ""

            if doPred:
                if expected == None:
                    perc = "UNDEF"
                    dev = "UNDEF"
                    sign = 1
                else:
                    diff = aveRate - expected
                    if expected != 0: 
                        perc = 100*diff/expected
                    else: 
                        perc = "INF"

                    if mse != 0:
                        dev = diff/mse
                        if abs(dev)>1000000: dev = ">1E6"
                    else: 
                        dev = "INF"

                    if perc > 0: 
                        sign = 1
                    else:
                        sign = -1

                    if perc != "INF":
                        perc = abs(perc)

                    if dev != "INF":
                        dev = abs(dev)
            else:
                sign = "" # No prediction, so no sign of a % diff
                perc = "" # No prediction, so no % diff
                dev = ""  # No prediction, so no deviation

            table[trigger] = [rate_val,avePSExpected,sign,perc,sign,dev,avePS,doPred,comment]

        return table

    def checkTriggers(self,avgLumi,trigger_table):
        # Reset counting variable
        self.normal = 0
        self.bad = 0

        tmp_list = []
        for elem in self.triggerList:
            if elem in tmp_list:
                continue
            else:
                tmp_list.append(elem)

        #Remove any duplicates
        for elem in self.fullL1HLTMenu:
            if elem in tmp_list:
                continue
            else:
                tmp_list.append(elem)

        #Check if the trigger is good or bad
        for trigger in tmp_list:
            #table[trigger] = [rate_val,avePSExpected,sign,perc,sign,dev,avPS,doPred,comment]
            #if not self.Rates.has_key(trigger): continue
            if not trigger_table.has_key(trigger): 
                print "Table missing triggers: %s" % trigger
                continue

            properAvePSRate = trigger_table[trigger][0]
            avePSExpected = trigger_table[trigger][1]
            perc = trigger_table[trigger][3]
            dev = trigger_table[trigger][5]
            avePS = trigger_table[trigger][6]
            doPred = trigger_table[trigger][7]

            if doPred:
                # Check for bad rates.
                if self.isBadTrigger(perc, dev, properAvePSRate, trigger[0:3]=="L1_"):
                    self.bad += 1
                    # Record if a trigger was bad
                    if not self.recordAllBadRates.has_key(trigger):
                        self.recordAllBadRates[trigger] = 0
                    self.recordAllBadRates[trigger] += 1
                    # Record consecutive bad rates
                    if not self.badRates.has_key(trigger):
                        self.badRates[trigger] = [1, True, properAvePSRate, avePSExpected, dev, avePS ]
                    else:
                        last = self.badRates[trigger]
                        self.badRates[trigger] = [ last[0]+1, True, properAvePSRate, avePSExpected, dev, avePS ]
                else:
                    self.normal += 1
                    # Remove warning from badRates
                    if self.badRates.has_key(trigger): del self.badRates[trigger]
                        
            else:
                if self.isBadTrigger("", "", properAvePSRate, trigger[0:3]=="L1_"):
                    self.bad += 1
                    # Record if a trigger was bad
                    if not self.recordAllBadRates.has_key(trigger):
                        self.recordAllBadRates[trigger] = 0
                    self.recordAllBadRates[trigger] += 1
                    # Record consecutive bad rates
                    if not self.badRates.has_key(trigger):
                        self.badRates[trigger] = [ 1, True, properAvePSRate, -999, -999, -999 ]
                    else:
                        last = self.badRates[trigger]
                        self.badRates[trigger] = [ last[0]+1, True, properAvePSRate, -999, -999, -999 ]
                else:
                    self.normal += 1
                    # Remove warning from badRates
                    if self.badRates.has_key(trigger): del self.badRates[trigger]

    # Use: Prints a section of a table, i.e. all the triggers in a trigger list (like usableHLTTriggers, otherHLTTriggers, etc)
    def printTableSection(self, triggerList, trigger_table, hasPred, avgLumi=0):
        tmp_dict = {}
        # Find the correct triggers
        for trigger in trigger_table:
            if trigger in triggerList:
                tmp_dict[trigger] = trigger_table[trigger]

        sorted_list = tmp_dict.items()
        #print "Trigger_table: ",trigger_table.keys()
        #table --> [trigger]<rate_val,avePSExpected,sign,perc,sign,dev,avPS,doPred,comment>
        if hasPred:
            if self.usePerDiff:
                #Sort by % diff
                sorted_list.sort(key=lambda tup: tup[1][3], reverse = True)
            else:
                #Sort by deviation
                sorted_list.sort(key=lambda tup: tup[1][5], reverse = True)
        else:
            sorted_list.sort(key=lambda tup: tup[1][0], reverse = True)

        #for trigger, rate, pred, sign, perdiff, dsign, dev, avePS, comment in self.tableData:
        for tup in sorted_list:
            trigger = tup[0]
            rate = trigger_table[trigger][0]
            pred = trigger_table[trigger][1]
            sign = trigger_table[trigger][2]
            perdiff = trigger_table[trigger][3]
            dsign = trigger_table[trigger][4]
            dev = trigger_table[trigger][5]
            avePS = trigger_table[trigger][6]
            doPred = trigger_table[trigger][7]
            comment = trigger_table[trigger][8]

            info = stringSegment("* "+trigger, self.spacing[0])
            info += stringSegment("* "+"{0:.2f}".format(rate), self.spacing[1])

            if pred != "":
                info += stringSegment("* "+"{0:.2f}".format(pred), self.spacing[2])
            else:
                info += stringSegment("", self.spacing[2])

            if perdiff == "":
                info += stringSegment("", self.spacing[3])
            elif perdiff == "INF":
                info += stringSegment("* INF", self.spacing[3])
            else:
                info += stringSegment("* "+"{0:.2f}".format(sign*perdiff), self.spacing[3])

            if dev == "":
                info += stringSegment("", self.spacing[4])
            elif dev == "INF" or dev == ">1E6":
                info += stringSegment("* "+dev, self.spacing[4])
            else:
                info += stringSegment("* "+"{0:.2f}".format(dsign*dev), self.spacing[4])

            info += stringSegment("* "+"{0:.2f}".format(avePS), self.spacing[5])
            info += stringSegment("* "+comment, self.spacing[6])

            # Color the bad triggers with warning colors
            #if avePS != 0 and self.isBadTrigger(perdiff, dev, rate/avePS, trigger[0:3]=="L1_"):
            if avePS != 0 and self.badRates.has_key(trigger):
                if not self.noColors: write(bcolors.WARNING) # Write colored text 
                print info
                if not self.noColors: write(bcolors.ENDC)    # Stop writing colored text
            # Don't color normal triggers
            else:
                print info

    def printTable(self,trigger_table,physicsActive,avgLumi,avgDeadTime,avgL1rate,PScol):
        self.printHeader()

        hasPred = physicsActive and self.mode == "collisions"
        # Print the triggers that we can make predictions for
        anytriggers = False
        if len(self.usableHLTTriggers) > 0:
            print '*' * self.hlength
            print "Predictable HLT Triggers (ones we have a fit for)"
            print '*' * self.hlength
            anytriggers = True
        self.L1 = False
        self.printTableSection(self.usableHLTTriggers,trigger_table,hasPred,avgLumi)
        if len(self.usableL1Triggers) > 0:
            print '*' * self.hlength
            print "Predictable L1 Triggers (ones we have a fit for)"
            print '*' * self.hlength
            anytriggers = True
        self.L1 = True
        self.printTableSection(self.usableL1Triggers,trigger_table,hasPred,avgLumi)


        #check the full menu for paths deviating past thresholds
        fullMenu_fits = False
        #for trigger in self.fullL1HLTMenu: self.getTriggerData(trigger, fullMenu_fits, avgLumi)

        # Print the triggers that we can't make predictions for (only for certain modes)
        if self.useAll or self.mode != "collisions" or self.InputFitHLT is None:
            print '*' * self.hlength
            print "Unpredictable HLT Triggers (ones we have no fit for or do not try to fit)"
            print '*' * self.hlength
            self.L1 = False
            self.printTableSection(self.otherHLTTriggers,trigger_table,False)
            self.printTableSection(self.otherL1Triggers,trigger_table,False)
            anytriggers = True
        if self.useL1:
            print '*' * self.hlength
            print "Unpredictable L1 Triggers (ones we have no fit for or do not try to fit)"
            print '*' * self.hlength
            self.L1 = True
            self.printTableSection(self.otherL1Triggers,trigger_table,False)
            anytriggers = True

        if not anytriggers:
            print '*' * self.hlength
            print "\n --- No useable triggers --- \n"

        # Print stream data
        if self.showStreams:
            print '*' * self.hlength
            streamSpacing = [ 50, 20, 25, 25, 25, 25 ]
            head = stringSegment("* Stream name", streamSpacing[0])
            head += stringSegment("* NLumis", streamSpacing[1])
            head += stringSegment("* Events", streamSpacing[2])
            head += stringSegment("* Stream rate [Hz]", streamSpacing[3])
            head += stringSegment("* File size [GB]", streamSpacing[4])
            head += stringSegment("* Stream bandwidth [GB/s]", streamSpacing[5])
            print head
            print '*' * self.hlength
            for name in sorted(self.streamData.keys()):
                count = 0
                streamsize = 0
                aveBandwidth = 0
                aveRate = 0
                for LS, rate, size, bandwidth in self.streamData[name]:
                    streamsize += size
                    aveRate += rate
                    aveBandwidth += bandwidth
                    count += 1
                if count > 0:
                    aveRate /= count
                    streamsize /= (count*1000000000.0)
                    aveBandwidth /= (count*1000000000.0)
                    row = stringSegment("* "+name, streamSpacing[0])
                    row += stringSegment("* "+str(int(count)), streamSpacing[1])
                    row += stringSegment("* "+str(int(aveRate*23.3*count)), streamSpacing[2])
                    row += stringSegment("* "+"{0:.2f}".format(aveRate), streamSpacing[3])
                    row += stringSegment("* "+"{0:.2f}".format(streamsize), streamSpacing[4])
                    row += stringSegment("* "+"{0:.5f}".format(aveBandwidth), streamSpacing[5])
                    if not self.noColors and aveRate > self.maxStreamRate: write(bcolors.WARNING) # Write colored text
                    print row
                    if not self.noColors and aveRate > self.maxStreamRate: write(bcolors.ENDC)    # Stop writing colored text 
                else: pass

        # Print PD data
        if self.showPDs:
            print '*' * self.hlength
            pdSpacing = [ 50, 20, 25, 25]
            head = stringSegment("* Primary Dataset name", pdSpacing[0])
            head += stringSegment("* NLumis", pdSpacing[1])
            head += stringSegment("* Events", pdSpacing[2])
            head += stringSegment("* Dataset rate [Hz]", pdSpacing[3])
            print head
            print '*' * self.hlength
            for name in self.pdData.keys():
                count = 0
                aveRate = 0
                for LS, rate in self.pdData[name]:
                    aveRate += rate
                    count += 1
                if count > 0:
                    aveRate /= count
                    row = stringSegment("* "+name, pdSpacing[0])
                    row += stringSegment("* "+str(int(count)), pdSpacing[1])
                    row += stringSegment("* "+str(int(aveRate*23.3*count)), pdSpacing[2])
                    row += stringSegment("* "+"{0:.2f}".format(aveRate), pdSpacing[3])
                    if not self.noColors and aveRate > self.maxPDRate: write(bcolors.WARNING) # Write colored text
                    print row
                    if not self.noColors and aveRate > self.maxPDRate: write(bcolors.ENDC)    # Stop writing colored text 
                else: pass

        # Closing information
        print '*' * self.hlength
        print "SUMMARY:"
        if self.mode == "collisions": print "Triggers in Normal Range: %s   |   Triggers outside Normal Range: %s" % (self.normal, self.bad)
        if self.mode == "collisions":
            print "Prescale column index:", 
            if PScol == 0:
                if not self.noColors and PScol == 0 and self.mode != "other": write(bcolors.WARNING) # Write colored text
                print PScol, "\t0 - Column 0 is an emergency column in collision mode, please select the proper column"
                if not self.noColors and PScol == 0 and self.mode != "other": write(bcolors.ENDC)    # Stop writing colored text 
            else:
                print PScol
        try:
            print "Average inst. lumi: %.0f x 10^30 cm-2 s-1" % (avgLumi)
        except:
            print "Average inst. lumi: Not available"
        print "Total L1 rate: %.0f Hz" % (avgL1rate)
        print "Average dead time: %.2f %%" % (avgDeadTime)
        try: 
            print "Average PU: %.2f" % (self.pu_ave)
        except: 
            print "Average PU: %s" % (self.pu_ave)
        print '*' * self.hlength

    def updateRunInfo(self, previous_run):
        #updates:
        #run number
        #trigger mode (and trigger lists)

        #self.runNumber, self.triggerMode = self.parser.getLatestRunInfo()
        self.runNumber, _, _, mode = self.parser.getLatestRunInfo()
        self.triggerMode = mode[0]
        
        is_new_run = ( previous_run != self.runNumber or self.runNumber < 0 ) 
        if is_new_run:
            print "Starting a new run: Run %s" % (self.runNumber)
            self.lastLS = 1
            self.currentLS = 0
            self.setMode()
            self.redoTriggerLists()
        
    def setMode(self):
        self.sendMailAlerts_dynamic = self.sendMailAlerts_static
        try:
            self.triggerMode = self.parser.getTriggerMode(self.runNumber)[0]
        except:
            self.triggerMode = "Other"
        if self.triggerMode.find("cosmics") > -1:
            self.mode = "cosmics"
        elif self.triggerMode.find("circulate") > -1:
            self.mode = "circulate"
        elif self.triggerMode.find("collisions") > -1:
            self.mode = "collisions"
        elif self.triggerMode == "MANUAL":
            self.mode = "MANUAL"
        elif self.triggerMode.find("highrate") > -1:
            self.mode = "other"
            #self.maxHLTRate = 100000
            #self.maxL1Rate = 100000
        else: self.mode = "other"

    # Use: Remakes the trigger lists
    def redoTriggerLists(self):
        self.redoTList = False
        # Reset the trigger lists
        self.usableHLTTriggers = []
        self.otherHLTTriggers = []
        self.usableL1Triggers = []
        self.otherL1Triggers = []
        self.fullL1HLTMenu = []
        # Reset bad rate records
        self.badRates = {}           # A dictionary: [ trigger name ] { num consecutive bad, trigger bad last check, rate, expected, dev }
        self.recordAllBadRates = {}  # A dictionary: [ trigger name ] < total times the trigger was bad >

        #set trigger lists automatically based on mode
        if not self.useAll and not self.userSpecTrigList:
            if self.mode == "cosmics" or self.mode == "circulate":
                self.triggerList = self.loadTriggersFromFile(self.cosmics_triggerList)
                print "monitoring triggers in: ", self.cosmics_triggerList
            elif self.mode == "collisions":
                self.triggerList = self.loadTriggersFromFile(self.collisions_triggerList)
                print "monitoring triggers in: ", self.collisions_triggerList
            else:
                self.triggerList = ""
                print "No lists to monitor: trigger mode not recognized"

            self.TriggerListL1 = []
            self.TriggerListHLT = []
            for triggerName in self.triggerList:
                if triggerName[0:3]=="L1_":
                    self.TriggerListL1.append(triggerName)
                elif triggerName[0:4]=="HLT_":
                    self.TriggerListHLT.append(triggerName)

        # Re-make trigger lists
        #print self.InputFitHLT.keys()
        for trigger in self.HLTRates.keys():
            if (not self.InputFitHLT is None and self.InputFitHLT.has_key(trigger)) and \
            (len(self.TriggerListHLT) !=0 and trigger in self.TriggerListHLT):
                self.usableHLTTriggers.append(trigger)
            elif trigger[0:4] == "HLT_" and (self.triggerList == "" or trigger in self.TriggerListHLT):
                self.otherHLTTriggers.append(trigger)
            elif (trigger[0:4] == "HLT_"): self.fullL1HLTMenu.append(trigger) 

        for trigger in self.L1Rates.keys():
            if (not self.InputFitL1 is None and self.InputFitL1.has_key(trigger)) and \
            (len(self.TriggerListL1) != 0 and trigger in self.TriggerListL1):
                self.usableL1Triggers.append(trigger)
            elif trigger[0:3] == "L1_" and (self.triggerList =="" or trigger in self.TriggerListL1):
                self.otherL1Triggers.append(trigger)
            elif (trigger[0:3] == "L1_"): self.fullL1HLTMenu.append(trigger)
                        
        self.getHeader()
        
    # Use: Prints the table header
    def printHeader(self):
        print "\n\n", '*' * self.hlength
        print "INFORMATION:"
        print "Run Number: %s" % (self.runNumber)
        print "LS Range: %s - %s" % (self.startLS, self.currentLS)
        print "Latest LHC Status: %s" % self.parser.getLHCStatus()[1]
        print "Number of colliding bunches: %s" % self.numBunches[0]
        print "Trigger Mode: %s (%s)" % (self.triggerMode, self.mode)
        print "Number of HLT Triggers: %s \nNumber of L1 Triggers: %s" % (self.totalHLTTriggers, self.totalL1Triggers)
        print "Number of streams:", self.totalStreams
        print '*' * self.hlength
        print self.header
        
    # Use: Returns whether a given trigger is bad
    # Returns: Whether the trigger is bad
    def isBadTrigger(self, perdiff, dev, psrate, isL1):
        if psrate == 0.0: return False
        if ( (self.usePerDiff and perdiff!="INF" and perdiff!="" and abs(perdiff)>self.percAccept) or (dev!="INF" and dev!="" and (dev==">1E6" or abs(dev)>self.devAccept)))\
        or (perdiff!="INF" and perdiff!="" and abs(perdiff)>self.percAccept and dev!="INF" and dev!="" and abs(dev)>self.devAccept)\
        or (isL1 and psrate>self.maxL1Rate)\
        or (not isL1 and psrate>self.maxHLTRate): return True
        
        return False
            
    def mailSender(self):
        if self.displayBadRates != 0:
            count = 0
            if self.displayBadRates != -1: write("First %s triggers that are bad: " % (self.displayBadRates)) 
            elif len(self.badRates) > 0 : write("All triggers deviating past thresholds from fit and/or L1 rate > %s Hz, HLT rate > %s Hz: " %(self.maxL1Rate,self.maxHLTRate))
            for trigger in self.badRates:
                if self.badRates[trigger][1]:
                    count += 1
                    write(trigger)
                    if count != self.displayBadRates-1:
                        write(", ")
                if count == self.displayBadRates:
                    write(".....")
                    break
            print ""

        # Print warnings for triggers that have been repeatedly misbehaving
        mailTriggers = [] # A list of triggers that we should mail alerts about
        for trigger in self.badRates:
            if self.badRates[trigger][1]:
                if self.badRates[trigger][0] >= 1:
                    print "Trigger %s has been out of line for more than %.1f minutes" % (trigger, float(self.badRates[trigger][0])*self.scale_sleeptime)
                # We want to mail an alert whenever a trigger exits the acceptable threshold envelope
                if self.badRates[trigger][0] == self.maxCBR:
                    mailTriggers.append( [ trigger, self.badRates[trigger][2], self.badRates[trigger][3], self.badRates[trigger][4], self.badRates[trigger][5] ] )
        # Send mail alerts
        if len(mailTriggers) > 0 and self.isUpdating:
            if self.sendMailAlerts_static and self.sendMailAlerts_dynamic: self.sendMail(mailTriggers)
            if self.sendAudioAlerts: audioAlert()

    # Use: Sleeps and prints out waiting dots
    def sleepWait(self):
        if not self.quiet: print "Sleeping for %.1f sec before next query" % (60.0*self.scale_sleeptime)
        for iSleep in range(20):
            if not self.quiet: write(".")
            sys.stdout.flush()
            time.sleep(3.0*self.scale_sleeptime)
        sys.stdout.flush()
        print ""
            
    # Use: Loads the fit data from the fit file
    # Parameters:
    # -- fitFile: The file that the fit data is stored in (a pickle file)
    # Returns: The input fit data
    def loadFit(self, fileName):
        if fileName == "":
            return None
        InputFit = {} # Initialize InputFit (as an empty dictionary)
        # Try to open the file containing the fit info
        try:
            pkl_file = open(fileName, 'rb')
            InputFit = pickle.load(pkl_file)
            pkl_file.close()
        except:
            # File failed to open
            print "Error: could not open fit file: %s" % (fileName)
        return InputFit

    # Use: Calculates the expected rate for a trigger at a given ilumi based on our input fit
    def calculateRate(self, triggerName, ilum):
        # Make sure we have a fit for the trigger
        if not self.L1 and (self.InputFitHLT is None or not self.InputFitHLT.has_key(triggerName)):
            return 0
        elif self.L1 and ((self.InputFitL1 is None) or not self.InputFitL1.has_key(triggerName)):
            return 0
        # Get the param list
        if self.L1: paramlist = self.InputFitL1[triggerName]
        else: paramlist = self.InputFitHLT[triggerName]
        # Calculate the rate
        if paramlist[0]=="exp": funcStr = "%s + %s*expo(%s+%s*x)" % (paramlist[1], paramlist[2], paramlist[3], paramlist[4]) # Exponential
        else: funcStr = "%s+x*(%s+ x*(%s+x*%s))" % (paramlist[1], paramlist[2], paramlist[3], paramlist[4]) # Polynomial
        fitFunc = TF1("Fit_"+triggerName, funcStr)
        if self.pileUp:
            if self.numBunches[0] > 0:
                return self.numBunches[0]*fitFunc.Eval(ilum/self.numBunches[0]*ppInelXsec/orbitsPerSec)
            else:
                return 0
        return fitFunc.Eval(ilum)

    # Use: Gets the MSE of the fit
    def getMSE(self, triggerName):
        if not self.L1 and (self.InputFitHLT is None or not self.InputFitHLT.has_key(triggerName)):
            return 0
        elif self.L1 and ((self.InputFitL1 is None) or not self.InputFitL1.has_key(triggerName)):
            return 0
        if self.L1: paramlist = self.InputFitL1[triggerName]
        else: paramlist = self.InputFitHLT[triggerName]
        if self.pileUp:
            return self.numBunches[0]*paramlist[5]
        return paramlist[5] # The MSE

    # Use: Sends an email alert
    # Parameters:
    # -- mailTriggers: A list of triggers that we should include in the mail, ( { triggerName, aveRate, expected rate, standard dev } )
    # Returns: (void)
    def sendMail(self, mailTriggers):
        mail = "Run: %d, Lumisections: %s - %s \n" % (self.runNumber, self.lastLS, self.currentLS)
        try: mail += "Average inst. lumi: %.0f x 10^30 cm-2 s-1\n" % (self.lumi_ave)
        except: mail += "Average inst. lumi: %s x 10^30 cm-2 s-1\n" % (self.lumi_ave)
        
        try: mail += "Average PU: %.2f\n \n" % (self.pu_ave)
        except: mail += "Average PU: %s\n \n" % (self.pu_ave)
        
        mail += "Trigger rates deviating from acceptable and/or expected values: \n\n"

        for triggerName, rate, expected, dev, ps in mailTriggers:
        
            if self.numBunches[0] == 0:
                mail += "\n %s: Actual: %s Hz\n" % (stringSegment(triggerName, 35), rate)
            else:
                if expected > 0:
                    try: mail += "\n %s: Expected: %.1f Hz, Actual: %.1f Hz, Unprescaled Expected/nBunches: %.5f Hz, Unprescaled Actual/nBunches: %.5f Hz, Deviation: %.1f\n" % (stringSegment(triggerName, 35), expected, rate, expected*ps/self.numBunches[0], rate*ps/self.numBunches[0], dev)
                    except: mail += "\n %s: Expected: %s Hz, Actual: %s Hz, Unprescaled Expected/nBunches: %s Hz, Unprescaled Actual/nBunches: %s Hz, Deviation: %s\n" % (stringSegment(triggerName, 35), expected, rate, expected*ps/self.numBunches[0], rate*ps/self.numBunches[0], dev)
                    mail += "  *referenced fit: <https://raw.githubusercontent.com/cms-tsg-fog/RateMon/master/Fits/2016/plots/%s.png>\n" % (triggerName)                    
                else:
                    try: mail += "\n %s: Actual: %.1f Hz\n" % (stringSegment(triggerName, 35), rate)
                    except: mail += "\n %s: Actual: %s Hz\n" % (stringSegment(triggerName, 35), rate)

            try:
                wbm_url = self.parser.getWbmUrl(self.runNumber,triggerName,self.currentLS)
                if not wbm_url == "-": mail += "  *WBM rate: <%s>\n" % (wbm_url)
            except:
                print "WBM plot url query failed"

                
        mail += "\nWBM Run Summary: <https://cmswbm.web.cern.ch/cmswbm/cmsdb/servlet/RunSummary?RUN=%s> \n\n" % (self.runNumber)
        mail += "Email warnings triggered when: \n"
        mail += "   - L1 or HLT rates deviate by more than %s standard deviations from fit \n" % (self.devAccept)
        mail += "   - HLT rates > %s Hz \n" % (self.maxHLTRate)
        mail += "   - L1 rates > %s Hz \n" % (self.maxL1Rate)

        print "--- SENDING MAIL ---\n"+mail+"\n--------------------"
        mailAlert(mail)

## ----------- End of class ShiftMonitor ------------ ##


