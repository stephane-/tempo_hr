from openerp.osv import fields, osv
import time
import datetime
from pprint import pprint
import pytz
import openerp
from openerp import tools, api
from openerp.osv import osv, fields
import dateutil


@api.model
def _tz_get(self):
    return [(tz,tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith('Etc/') else '_')]

@api.multi
def _get_tz_offset(self, name, args):
    return dict(
        (p.id, datetime.datetime.now(pytz.timezone(p.tz or 'GMT')).strftime('%z'))
        for p in self)


class tempo_hr_time(osv.osv):
    _name = "hr.employee"
    _inherit = 'hr.employee'

    _columns = {
        'tz': fields.selection(
            _tz_get,
            'Timezone',
            size=64,
            help="The partner's timezone, used to output proper date and time values inside printed reports. "
            "It is important to set a value for this field. You should use the same timezone "
            "that is otherwise used to pick and render date and time values: your computer's timezone."),
        'freetime': fields.integer('Free minute by days'),
        'tz_offset': fields.function(_get_tz_offset,
                                     type='char',
                                     size=5,
                                     string='Timezone offset',
                                     invisible=True),
    }

    _defaults = {
        'tz': api.model(lambda self: self.env.context.get('tz', False)),
    }


class tempo_hr_holidays(osv.osv):
    _name = "hr.holidays"
    _inherit = 'hr.holidays'

    def _compute_number_of_days_abs(self, cr, uid, ids, name, args, context=None):
        result = {}
        for hol in self.browse(cr, uid, ids, context=context):
            result[hol.id] = hol.number_of_days_temp
        return result

    _columns = {
        'number_of_days_abs': fields.function(_compute_number_of_days_abs, string='Number of Days', store=True),
    }


class tempo_hr_calc(osv.osv):
    _name = "hr.attendance"
    _inherit = 'hr.attendance'

    def get_real_times(self, cr, uid, leaves, din, dout, tz):
        res = list()
        for l in self.pool.get('hr.holidays').browse(cr, uid, leaves):
            if l.date_to and l.date_from:
                lto = datetime.datetime\
                    .strptime(l.date_to, '%Y-%m-%d %H:%M:%S')
                lfrom = datetime.datetime\
                    .strptime(l.date_from, '%Y-%m-%d %H:%M:%S')
                hto = lto.strftime("%H.%M")
                hfrom = lfrom.strftime("%H.%M")

                lfrom = self.get_date(hfrom, lfrom.date(), "Etc/UTC")
                lto = self.get_date(hto, lto.date(), "Etc/UTC")

                if lfrom <= din and lto >= dout:
                    return list()
                elif lfrom <= din and lto >= din:
                    din = lto
                elif lfrom <= dout and lto >= dout:
                    dout = lfrom
                elif lfrom > din and lto < dout:
                    d1 = self.get_real_times(cr, uid, leaves, din, lfrom, tz)
                    d2 = self.get_real_times(cr, uid, leaves, lto, dout, tz)
                    for d in d1:
                        res.append(d)
                    for d in d2:
                        res.append(d)
                    return res
        res.append([din, dout])
        return res

    def get_date(self, hour, date, tz):
        if tz is not False:
            tz = pytz.timezone(tz)
        else:
            tz = pytz.utc

        time_date = str(hour).split('.')
        date = str(date) + " " + str(time_date[0]) + ":"
        if len(str(time_date[1])) == 1:
            date += "0" + str(time_date[1])
        else:
            date += str(time_date[1])
        name = datetime.datetime\
            .strptime(date, '%Y-%m-%d %H:%M')
        date = tz.localize(name, is_dst=None)
        date = date.astimezone(pytz.utc)
        return date

    def cron_tempo_hr(self, cr, uid, context=None):
        today = datetime.datetime.now()
        employees = self.pool.get('hr.employee')\
            .search(cr, uid,
                    [('active', '=', False)])
        for employee in self.pool.get('hr.employee').browse(cr, uid, employees):
            last_sign = employee.last_sign
            last_sign_date = ""

            if type(last_sign) is str:
                last_sign = datetime.datetime.strptime(last_sign, '%Y-%m-%d %H:%M:%S')
                last_sign_date = str(last_sign.date())

            if last_sign_date != str(today.date()):
                working_days = employee.contract_id\
                    .working_hours.attendance_ids
                for day in working_days:
                    if int(day.dayofweek) == today.weekday():

                        holidays = self.pool.get('public.holidays.holidays')\
                            .is_holiday(cr, uid, today.date())
                        if holidays is False:
                            date_in = self.get_date(
                                day.hour_from, today.date(), employee.tz)
                            date_out = self.get_date(
                                day.hour_to, today.date(), employee.tz)

                            leaves = self.pool.get('hr.holidays')\
                                .search(cr, uid,
                                        [('employee_id', '=', employee.id)])
                            real_worked_time = self\
                                .get_real_times(
                                    cr, uid, leaves,
                                    date_in, date_out, employee.tz)

                            for t in real_worked_time:
                                    vals = {
                                        'action': 'sign_in',
                                        'name': t[0],
                                        'employee_id': employee.id,
                                    }
                                    hr_attendance = self\
                                        .pool.get('hr.attendance')
                                    hr_attendance.create(cr, uid,
                                                         vals, context=context)
                                    vals = {
                                        'action': 'sign_out',
                                        'name': t[1],
                                        'employee_id': employee.id,
                                    }
                                    hr_attendance = self\
                                        .pool.get('hr.attendance')
                                    hr_attendance.create(cr, uid,
                                                         vals, context=context)
        return None

    def calc_worked_hours(self, cr, uid, obj, context=None):

        last_signin_id = self.search(cr, uid, [
            ('employee_id', '=', obj.employee_id.id),
            ('name', '<', obj.name), ('action', '=', 'sign_in')
        ], limit=1, order='name DESC')
        if last_signin_id:
            last_signin = self.browse(cr, uid, last_signin_id, context=context)[0]
            last_signin_datetime = datetime.datetime.strptime(last_signin.name, '%Y-%m-%d %H:%M:%S')
            signout_datetime = datetime.datetime.strptime(obj.name, '%Y-%m-%d %H:%M:%S')
            workedhours_datetime = (signout_datetime - last_signin_datetime)
            seconds = workedhours_datetime.seconds % 60
            minutes = (workedhours_datetime.seconds / 60) % 60
            hours = (workedhours_datetime.seconds / 60) / 60
            minutes_dec = float(minutes) / 60
            seconds_dec = float(seconds) / 3600
            res = float(hours) + float(minutes_dec) + float(seconds_dec)
        else:
            res = False
        return res

    def _worked_hours_compute(self, cr, uid, ids, fieldnames, args, context=None):
        res = {}
        for obj in self.browse(cr, uid, ids, context=context):
            if obj.action == 'sign_in':
                res[obj.id] = 0
            elif obj.action == 'sign_out':
                res[obj.id] = self.calc_worked_hours(cr, uid,
                                                     obj, context=context)
        return res

    def _calendar_start(self, cr, uid, ids, fieldnames, args, context=None):
        res = {}
        for obj in self.browse(cr, uid, ids, context=context):
            if obj.action == 'sign_in':
                res[obj.id] = None
            elif obj.action == 'sign_out':
                date_stop = datetime.datetime.strptime(obj.name, '%Y-%m-%d %H:%M:%S')
                date_delay = self.calc_worked_hours(cr, uid,
                                                    obj, context=context)
                hours = int(date_delay)
                minutes = int((float(date_delay) - float(hours)) * float(60))
                date_start = date_stop - datetime.timedelta(hours=hours, minutes=minutes)
                res[obj.id] = date_start
        return res

    def _export_date(self, cr, uid, ids, fieldnames, args, context=None):
        res = {}

        current_user = self.pool.get('res.users')\
            .browse(cr, uid, uid, context=context)

        for obj in self.browse(cr, uid, ids, context=context):
                timeUTC = datetime\
                    .datetime.strptime(obj.name, '%Y-%m-%d %H:%M:%S')
                timezoneLocal = pytz.timezone(current_user.tz)
                utc = pytz.utc
                timeLocal = utc.localize(timeUTC).astimezone(timezoneLocal)

                res[obj.id] = timeLocal
        return res

    def name_get(self, cr, uid, ids, context=None):
        if context is None:
            context = {}
        res = {}
        for obj in self.browse(cr, uid, ids, context=context):
            res[obj.id] = str(obj.employee_id.name)
        return res


    _columns = {
        'worked_hours': fields.function(_worked_hours_compute, type='float', string='Heures travaillees', store=True),
        'calendar_start': fields.function(_calendar_start, type='datetime', string='Calendar start', store=True),
        'export_date': fields.function(_export_date, type='datetime', string='Export date')
    }


class tempo_hr_plan(osv.osv):
    _name = "tempo_hr"

    def get_real_times(self, cr, uid, leaves, din, dout, tz):
        res = list()
        for l in self.pool.get('hr.holidays').browse(cr, uid, leaves):
            if l.date_to and l.date_from:
                lto = datetime.datetime\
                    .strptime(l.date_to, '%Y-%m-%d %H:%M:%S')
                lfrom = datetime.datetime\
                    .strptime(l.date_from, '%Y-%m-%d %H:%M:%S')
                hto = lto.strftime("%H.%M")
                hfrom = lfrom.strftime("%H.%M")

                lfrom = self.get_date(hfrom, lfrom.date(), "Etc/UTC")
                lto = self.get_date(hto, lto.date(), "Etc/UTC")

                if lfrom <= din and lto >= dout:
                    return list()
                elif lfrom <= din and lto >= din:
                    din = lto
                elif lfrom <= dout and lto >= dout:
                    dout = lfrom
                elif lfrom > din and lto < dout:
                    d1 = self.get_real_times(cr, uid, leaves, din, lfrom, tz)
                    d2 = self.get_real_times(cr, uid, leaves, lto, dout, tz)
                    for d in d1:
                        res.append(d)
                    for d in d2:
                        res.append(d)
                    return res
        res.append([din, dout])
        return res

    def get_date(self, hour, date, tz):
        if tz is not False:
            tz = pytz.timezone(tz)
        else:
            tz = pytz.utc

        time_date = str(hour).split('.')
        date = str(date) + " " + str(time_date[0]) + ":"
        if len(str(time_date[1])) == 1:
            date += "0" + str(time_date[1])
        else:
            date += str(time_date[1])
        name = datetime.datetime\
            .strptime(date, '%Y-%m-%d %H:%M')
        date = tz.localize(name, is_dst=None)
        date = date.astimezone(pytz.utc)
        return date

    def cron_plan_tempo_hr(self, cr, uid, context=None):
        # Remove all values
        hr_tempo = self.pool.get('tempo_hr')
        ids = hr_tempo.search(cr, uid, [], context=context)
        hr_tempo.unlink(cr, uid, ids, context=context)

        hr_tempo_summary = self.pool.get('tempo_hr_summary')
        ids = hr_tempo_summary.search(cr, uid, [], context=context)
        hr_tempo_summary.unlink(cr, uid, ids, context=context)

        date_today = datetime.date.today()

        employees = self.pool.get('hr.employee')\
            .search(cr, uid,
                    ['|', ('active', '=', False),
                     ('active', '=', True)])
        for employee in self.pool.get('hr.employee').browse(cr, uid, employees):
            working_days = employee.contract_id\
                .working_hours.attendance_ids
            year_now = datetime.datetime.now().year
            current_date = datetime.date(year_now, 1, 1)
            date_stop = datetime.date(year_now + 1, 12, 31)

            if working_days is not None:
                while current_date <= date_stop:
                    holidays = self.pool.get('public.holidays.holidays')\
                        .is_holiday(cr, uid, current_date)
                    if holidays is False:
                        for day in working_days:
                            if int(day.dayofweek) == current_date.weekday():
                                date_in = self.get_date(
                                    day.hour_from, current_date, employee.tz)
                                date_out = self.get_date(
                                    day.hour_to, current_date, employee.tz)

                                leaves = self.pool.get('hr.holidays')\
                                    .search(cr, uid,
                                            [('employee_id', '=', employee.id)])
                                real_worked_time = self\
                                    .get_real_times(
                                        cr, uid, leaves,
                                        date_in, date_out, employee.tz)

                                for t in real_worked_time:
                                    wd = (t[1] - t[0])
                                    seconds = wd.seconds % 60
                                    minutes = (wd.seconds / 60) % 60
                                    hours = (wd.seconds / 60) / 60
                                    min_dec = float(minutes) / 60
                                    sec_dec = float(seconds) / 3600
                                    planned_hours = float(hours)\
                                        + float(min_dec)\
                                        + float(sec_dec)
                                    vals = {
                                        'employee_id': employee.id,
                                        'date_start': t[0],
                                        'date_stop': t[1],
                                        'date': t[0].date(),
                                    }
                                    hr_tempo = self.pool.get('tempo_hr')
                                    hr_tempo.create(cr, uid, vals, context=context)

                                    #Add summary
                                    if t[0].date() <= date_today:
                                        current_summary = self.pool.get('tempo_hr_summary')
                                        search = ['&',
                                                 ['employee_id', '=', employee.id],
                                                 ['date', '=', t[0].date()]]
                                        ids = current_summary\
                                            .search(cr, uid, search,
                                                    context=context)

                                        if ids:
                                            for plan in self.pool.get('tempo_hr_summary').browse(cr, uid, ids):
                                                plan.planned_hours += planned_hours
                                        else:
                                            vals_sum = {
                                                'employee_id': employee.id,
                                                'date': t[0].date(),
                                                'planned_hours': planned_hours,
                                            }
                                            hr_tempo_summary = self.pool.get('tempo_hr_summary')
                                            hr_tempo_summary.create(cr, uid, vals_sum, context=context)

                    current_date = current_date + datetime.timedelta(days=1)
        return None

    _columns = {
        'employee_id': fields.many2one('hr.employee', "Employee", required=True),
        'date_start': fields.datetime('Start Date', required=True),
        'date_stop': fields.datetime('End Date', required=True),
        'date': fields.date('Date', required=True),
    }


class tempo_hr_summary(osv.osv):
    _name = "tempo_hr_summary"

    def calc_worked_hours(self, cr, uid, obj, context=None):
        dates = self.pool.get('hr.attendance')
        last_signin_id = dates.search(cr, uid, [
            ('employee_id', '=', obj.employee_id.id),
            ('name', '<', obj.name), ('action', '=', 'sign_in')
        ], limit=1, order='name DESC')
        if last_signin_id:
            last_signin = self.pool.get('hr.attendance')\
                .browse(cr, uid, last_signin_id, context=context)[0]
            last_signin_datetime = datetime.datetime.strptime(last_signin.name, '%Y-%m-%d %H:%M:%S')
            signout_datetime = datetime.datetime.strptime(obj.name, '%Y-%m-%d %H:%M:%S')
            workedhours_datetime = (signout_datetime - last_signin_datetime)
            res = workedhours_datetime.seconds
        else:
            res = False
        return res

    def _worked_hours_compute(self, cr, uid, ids, fieldnames, args, context=None):
        res = {}

        for obj in self.browse(cr, uid, ids, context=context):
            dates = self.pool.get('hr.attendance')
            date = datetime.datetime.strptime(obj.date, '%Y-%m-%d')
            date2 = date + datetime.timedelta(days=1)
            search = ['&',
                     ['employee_id', '=', obj.employee_id.id],
                     ['name', '>=', str(date)],
                     ['name', '<', str(date2)]]
            ids = dates.search(cr, uid, search, context=context)
            seconds = 0

            for attend in self.pool.get('hr.attendance').browse(cr, uid, ids):
                if attend.action == 'sign_in':
                    seconds += 0
                elif attend.action == 'sign_out':
                    seconds += self.calc_worked_hours(cr, uid,
                                                attend, context=context)

            if seconds != 0:
                seconds += obj.employee_id.freetime * 60
            minutes = (seconds / 60) % 60
            hours = seconds / 3600
            seconds_real = seconds % 60
            minutes_dec = minutes / 60.0
            seconds_dec = seconds_real / 3600.0
            res[obj.id] = float(hours) + float(minutes_dec) + float(seconds_dec)
        return res

    def _diff_hours_compute(self, cr, uid, ids, fieldnames, args, context=None):
        res = {}
        for obj in self.browse(cr, uid, ids, context=context):
            res[obj.id] = obj.worked_hours - obj.planned_hours
        return res

    def _get_update_ids(self, cr, uid, ids, context=None):
        return ids

    _columns = {
        'employee_id': fields.many2one('hr.employee', "Employee", required=True),
        'date': fields.date('Date', required=True),
        'planned_hours': fields.float('Heures planifiees', required=True),
        'worked_hours': fields.function(_worked_hours_compute, type='float', string='Heures travaillees', store=True),
        'diff_hours': fields.function(_diff_hours_compute, type='float', string="Differences d'heures",
                                      store={'tempo_hr_summary': (_get_update_ids, ['worked_hours', 'planned_hours'], 10)}),
    }