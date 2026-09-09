# Paste list for a live demo

Post these one at a time into the test channel. Each line says what the pipeline
should do with it, so a wrong answer is visible immediately.

### 1. expect: **ticket** · dc `NQS`
```
Raising for captain panel issue
DC Code: NQS
Hub Name: Narsinghpur LMDC
Captain: Rakesh Yadav
Mobile: 9900041273
Kapture ID: 4788325630026
Issue: panel me login nahi ho raha, OTP hi nahi aa raha subah se. 3 baar try kiya
Escalated to: sandeep.mahto@meesho.com
```

### 2. expect: **ticket**
```
bhai aaj 6 FE ek saath chutti pe chale gaye teen log gaon gaye hain tyohar ke liye aur do ne kal se aana hi band kar diya bina bataye itne kam logo me 2800 shipment kaise nikalega samajh nahi aa raha hai koi temporary manpower ka arrangement karwa do warna aaj ka pura delivery gadbad ho jayega
```

### 3. expect: **reject_entity**
```
sheet ke hub column me NQSX aa raha hai, ye kaunsa DC hai koi bata do? mapping file me kahi nahi mil raha 🤷
```

### 4. expect: **ticket**
```
@channel DC landing time is late again, 3rd day continuously. Aaj bhi 0630 wali gaadi 11 baje lagi, pura sorting slip ho gaya aur FE khade rahe. escalate please
```

### 5. expect: **gate**
```
k
```

### 6. expect: **informational** · dc `CXL, NXG, PJ2`
```
@channel Heavy rain alert - Mumbai / Bhiwandi belt
Subah se non stop barish hai, Kurla aur Kalher side waterlogging hai. LMDC PJ2, NXG aur CXL ke pilots ko bola hai safe route se hi chalein, koi force delivery nahi.
Aaj attempt % dip karega, expected hai. Evening me consolidated update dunga.
```

### 7. expect: **ticket** · dc `PJ2`
```
gentle reminder 🙏
ticket 4788325630026 - August ka invoice abhi tak generate nahi ho raha hai, 5 din ho gaye raise kiye hue
DC Code: PJ2
koi update please
```

### 8. expect: **ticket** · dc `HY9, IAM, MFC, VN4`
```
HY9 , IAM , VN4 hub not recive load frm MFC since yesterday nite
gaadi kaha hai koi bata do
3 hub ka dispatch ruka pada hai @channel
```

### 9. expect: **gate**
```
haan bhai ho gaya thank you 🙏
```

### 10. expect: **ticket** · dc `PJ2`
```
thanks but PJ2 is still down na, since morning koi scan nahi ho raha, inbound trucks khade hai gate pe
```

### 11. expect: **informational** · dc `CKH, K6L, YNM`
```
*Weather advisory - 09 Sep*
- IMD orange alert, heavy rain Konkan belt till 6 PM
- DCs impacted: CKH, K6L, YNM
- Line haul BLR-MUM 3-4 hrs late chal sakta hai
- Riders ko raincoat aur cover issue kar diya gaya hai
- FYI only, abhi koi action nahi chahiye
```

### 12. expect: **gate**
```
?
```

### 13. expect: **informational** · dc `AML, K6L, MX1, R2F, RX3, YNM`
```
Heavy rain alert - IMD ne orange alert diya hai coastal belt ke liye aaj raat se
affected hubs : RX3, R2F, K6L, YNM, AML, MX1
line haul departure 3-4 hrs late ho sakta hai, apne apne DC pe pilots ko abhi inform kar dijiye
abhi koi closure nahi hai, sirf heads up hai
```

### 14. expect: **informational** · dc `IQU, NQS`
```
sabhi ko inform kar do bhiwandi aur aas paas raat se bahut tez baarish ho rahi hai road pe paani bhara hua hai IQU aur NQS dono taraf ki gaadiyan late chalengi aaj koi escalation mat karna hum khud monitor kar rahe hain
```

### 15. expect: **ticket** · dc `J93`
```
App error
DC: J93
FE mobile: 9900054390
Screen: trip start karte hi app crash
```

### 16. expect: **ticket** · dc `PJ2`
```
LM tracker se copy kar raha hu, aaj ka pending -
DC Code | Waybill | Kapture ID | Contact | Status
PJ2 | VL0084870753799 | 4790118273645 | 9900018842 | Load not received, bag scan nahi hua 06/09 se
Isko aaj hi close karwao
```

### 17. expect: **gate**
```
Noted ✅
1. done
2. done
3. done
```

### 18. expect: **reject_entity**
```
Team, aaj se isi format me raise karna, warna ticket nahi banega:
DC Code: ___
Hub Name: ___
Mobile: 9xxxxxxxxx
Kapture ID: ___
Waybill: VLxxxxxxxxxxxxx
Issue in one line: ___
Priority: P1/P2/P3
```

### 19. expect: **reject_entity**
```
Kal ke gate pass ke liye detail -
Vehicle No: HR55AB1234
Vehicle Type: 32 ft SXL
Trip Ref: TRP-00918273
Driver: Mahesh
Pincode: 122001
Reporting time: 06:30 AM
```

### 20. expect: **ticket**
```
Team pls check -
dc code: pj2
hub: mfc
issue: FE app me trip list load nahi ho rahi, subah se 6 FE gate pe khade hai
```

### 21. expect: **ticket** · dc `CXL, NXG, R2F`
```
DC Code: ALL
Subject: Diwali week manpower planning
Saare hub me 25 Oct se 3 Nov tak ~30% extra FE chahiye, hiring plan aaj share karo
Current gap: NXG - 12 FE, R2F - 9 FE, CXL - 14 FE
```

### 22. expect: **ticket** · dc `YWW`
```
Pilot onboarding stuck hai portal pe
DC Code: YWW
Pilot Name: Imran Shaikh
Pilot ID: 61240938
Mobile: 9900085512
Docs uploaded: Yes
Bank verification: pending 4 din se
Status: 'Under review' hi dikha raha hai
Kapture ID: 4791220845113
```
