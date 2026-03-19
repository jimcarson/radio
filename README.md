*Problem statement:*
Logging QSLs accurately is surprisingly complicated.  One of the annoyances I have had is working mobile, either me or the other person, results in more data discrepancies in QRZ.   The way QRZ presents these makes it super tedious to correct via the GUI, averaging 8-13 clicks for each call sign (and a rate of 40 QSOs a month).

This script attempts to automate an "Accept all" option.  

To avoid hammering the QRZ API, we rely on being able to first download a full ADIF file from the site, then reference that as our place to look for the QRZ identifier used to update records. Mathces are done by call signs, band, times, and mode.  (I have not tested this for roving operations where a station is on a county, grid or state boundary and N1MM loggs multiple contacts.)  To export the file:


1. Go to logbook.qrz.com
2. Select your logbook
3. Click Settings
4. Click "Export"  under ADIF Import/Export
5. This will take a few minutes.  When done, click Settings again.
6. In File Import / Export History, you will see the option to Download the full export file.  

Next, go through each of the entities to correct:

    Awards -> Click on your call sign -> Click on United States Counties Award
    Awards -> Click on your call sign -> Click on Grid Squared Award
    Awards -> Click on your call sign -> Click on United States Counties

If there are discrepancies, QRZ will show something like this:

Click on "Details" to expand the table.   Now copy and paste the text into an Excel file, a sample is supplied.  (It is okay to overwrite the headers, I purposely ignore anything after "Entered".) 

    QSO Date 	QSO With 	DE 	You Entered county 	Other Party Entered county 	
    2025-12-04 23:43:00 	KL7RW 	WT8P 		Anchorage Borough, AK 	
    2025-08-11 02:22:00 	KL4RL 	WT8P 		Anchorage Borough, AK 	
    2025-12-19 01:02:00 	KL7J 	WT8P 		Kenai Peninsula Borough, AK 	
    2025-12-22 23:50:00 	KL7DRC 	WT8P 		Southeast Fairbanks Borough, AK 	
    2025-12-27 00:20:00 	KL5PF 	WT8P 		Fairbanks North Star Borough, AK 	
    2026-02-22 02:17:00 	KL7AC 	WT8P 		Fairbanks North Star Borough, AK

Below are examples of running.  I strongly recommend you do a --dry-run first to see if it's matching with your expectation.

# Correct other party's fields, but do a dry run
python resolve_qrz_discrepancies.py --xlsx qrz_errors.xlsx --adif yourcall.adi --key YOUR-QRZ-XML-KEY --call YOURCALL --dry-run

# Correct other party's fields 
python resolve_qrz_discrepancies.py --xlsx qrz_errors.xlsx --adif yourcall.adi --key YOUR-QRZ-XML-KEY --call YOURCALL

# Correct your own station's fields via Excel
python resolve_qrz_discrepancies.py --xlsx my_corrections.xlsx --adif yourcall.adi --key YOUR-QRZ-XML-KEY --call YOURCALL --my-station

# Correct your own station's fields via CSV
python resolve_qrz_discrepancies.py --input-csv my_corrections.csv --adif yourcall.adi --key YOUR-QRZ-XML-KEY --call YOURCALL --my-station


NOTES:
   QRZ.com's user interface will report Alaskan counties as "Bourough", which differs from what is contained in the ADIF file.  For that state only, we strip off Borough so the record matches.
   In some cases, there are bad data accepted from the other person.  You can mark it, and the program will skip it, but if you don't, it will try and fail silently.  For example, if another person has, somehow, reported their state as "IND" instead of "IN", there's nothing we can do about the remote user.
