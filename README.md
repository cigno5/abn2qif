# abn2qif
ABNAMRO transactions converter for HomeBank QIF file format

`abn2qif` is able to convert a zipped CAMT.053 file directly exported from ABN AMRO bank website into
`.qif` file readable by [HomeBank](http://homebank.free.fr/), the popular personal financing application.

The script needs a configuration file in order to convert IBAN codes into HomeBank accounts and understand transfer
between the accounts.

`abn2qif` requires python 3.5+ to be executed.

## usage

```bash
python abn2qif.py configuration_file CAMT.053_file [CAMT.053_file_2 CAMT.053_file_3 ...] 
```
m
