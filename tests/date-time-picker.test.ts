import test from 'node:test';
import assert from 'node:assert/strict';
import {validDate,monthCells,shiftDate,singaporeLocal,selectedValue,valueError} from '../frontend/date-time-picker.js';

test('calendar dates validate actual Gregorian days and leap years',()=>{
 for(const date of ['2026-10-21','2024-02-29','2026-12-31'])assert(validDate(date));
 for(const date of ['2026-02-29','2026-04-31','2026-13-01','2026-00-01','2026-10-00','2026-1-1',''])assert(!validDate(date));
});
test('month grid is Monday-first, stable across leap day and year rollover, and contains no duplicate dates',()=>{
 for(const month of ['2026-10','2024-02','2026-12','2027-01']){const cells=monthCells(month);assert.equal(cells.length,42);assert.equal(new Set(cells.map(c=>c.date)).size,42);assert(cells.every(c=>validDate(c.date)));assert.equal(new Date(cells[0].date+'T12:00:00Z').getUTCDay(),1);}
 assert.equal(monthCells('2024-02').filter(c=>c.in_month).length,29);assert.equal(monthCells('2026-02').filter(c=>c.in_month).length,28);assert.equal(shiftDate('2026-12-31',1),'2027-01-01');assert.equal(shiftDate('2024-03-01',-1),'2024-02-29');
});
test('clock and form values use Singapore local time without machine-timezone-dependent date conversion',()=>{
 assert.equal(singaporeLocal(new Date('2026-10-20T16:05:00Z')),'2026-10-21T00:05');assert.equal(selectedValue('2026-10-21',13,5,true),'2026-10-21T13:05');assert.equal(selectedValue('2026-10-21',13,5,false),'2026-10-21');assert.equal(selectedValue(null,13,5,true),'');
});
test('confirm rejects impossible dates/times and respects inclusive minimum/maximum boundaries',()=>{
 assert(valueError('2026-02-29'));assert(valueError('2026-10-21T24:00'));assert(valueError('2026-10-21T13:60'));assert(valueError('2026-10-20T12:59','2026-10-20T13:00'));assert.equal(valueError('2026-10-20T13:00','2026-10-20T13:00'),'');assert.equal(valueError('2026-10-21','','2026-10-21'),'');assert(valueError('2026-10-22','','2026-10-21'));
});
