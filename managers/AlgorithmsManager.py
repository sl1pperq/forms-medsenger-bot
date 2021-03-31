from datetime import datetime, timedelta

from helpers import log, generate_description
from managers.ContractsManager import ContractManager
from managers.FormManager import FormManager
from managers.Manager import Manager
from managers.MedicineManager import MedicineManager
from models import Patient, Contract, Algorithm


class AlgorithmsManager(Manager):
    def __init__(self, *args):
        super(AlgorithmsManager, self).__init__(*args)

    def get(self, algorithm_id):
        return Algorithm.query.filter_by(id=algorithm_id).first_or_404()

    def detach(self, template_id, contract):
        algorithms = list(filter(lambda x: x.template_id == template_id, contract.patient.algorithms))

        for algorithm in algorithms:
            algorithm.delete()

        self.__commit__()

    def attach(self, template_id, contract, setup=None):
        algorithm = self.get(template_id)

        if algorithm:
            new_algorithm = algorithm.clone()
            new_algorithm.contract_id = contract.id
            new_algorithm.patient_id = contract.patient.id

            if setup:
                for block in algorithm.criteria:
                    for criteria in block:
                        if criteria.get('ask_value') and setup.get(criteria['value_code']):
                            criteria['value'] = setup.get(criteria['value_code'])

            self.db.session.add(new_algorithm)
            self.__commit__()

            return True
        else:
            return False

    def remove(self, id, contract):

        algorithm = Algorithm.query.filter_by(id=id).first_or_404()

        if algorithm.contract_id != contract.id and not contract.is_admin:
            return None

        Algorithm.query.filter_by(id=id).delete()

        self.__commit__()
        return id

    def get_templates(self):
        return Algorithm.query.filter_by(is_template=True).all()

    def get_values(self, category_name, mode, contract_id, hours=1):
        if mode == 'value':
            answer = self.medsenger_api.get_records(contract_id, category_name, group=True)
        else:
            answer = self.medsenger_api.get_records(contract_id, category_name,
                                                    time_from=int(
                                                        (datetime.now() - timedelta(hours=hours)).timestamp()))

        values = list(map(lambda x: x['value'], answer['values']))
        ids = list(map(lambda x: x['id'], answer['values']))

        if not values:
            return None, None

        if mode == 'value':
            return values, ids
        if mode == 'sum':
            return [sum(values)], None
        if mode == 'difference':
            return [max(values) - min(values)], None
        if mode == 'delta':
            return [values[-1] - values[0]], None
        if mode == 'average':
            return [sum(values) / len(values)], None
        if mode == 'max':
            return [max(values)], None
        if mode == 'min':
            return [min(values)], None

        return None, None

    def check_values(self, left, right, sign):
        if sign == 'greater':
            return left > right
        if sign == 'less':
            return left < right
        if sign == 'greater_or_equal':
            return left >= right
        if sign == 'less_or_equal':
            return left <= right
        if sign == 'equal':
            return left == right
        if sign == 'not_equal':
            return left != right
        if sign == 'contains':
            return right in left

        return False

    def check_criteria(self, criteria, contract_id, buffer, descriptions, category_names):
        category_name = criteria.get('category')
        mode = criteria.get('left_mode')

        if mode != 'time':

            ids = None
            left_values, ids = self.get_values(category_name, criteria['left_mode'], contract_id,
                                               criteria.get('left_hours'))

            if criteria['right_mode'] == 'value':
                right_values = [criteria.get('value')]
            else:
                right_category = criteria.get('right_category')
                if right_category:
                    right_values, _ = self.get_values(right_category, criteria['right_mode'], contract_id,
                                                      criteria.get('right_hours'))
                else:
                    right_values, _ = self.get_values(category_name, criteria['right_mode'], contract_id,
                                                      criteria.get('right_hours'))

            if not right_values or not left_values:
                return False

            found = False

            for i in range(len(left_values)):
                lvalue = left_values[i]

                for rvalue in right_values:
                    result = self.check_values(lvalue, rvalue, criteria['sign'])

                    if result:
                        description = generate_description(criteria, lvalue, rvalue, category_names)
                        descriptions.append(description)

                        if ids:
                            buffer.append({
                                "id": ids[i],
                                "comment": description
                            })

                    if result:
                        found = True

            return found

        else:
            date = criteria.get('value')
            add_hours = criteria.get('right_hours')
            sign = criteria.get('sign')

            date_obj = datetime.strptime(date, '%Y-%m-%d') + timedelta(hours=add_hours)
            now_obj = datetime.now()

            if sign == 'equal' and 0 <= (now_obj - date_obj).total_seconds() < 60 * 60:
                return True
            if sign == 'greater' and (now_obj - date_obj).total_seconds() > 0:
                return True
            if sign == 'less' and (now_obj - date_obj).total_seconds() < 0:
                return True
            return False

    def run_action(self, action, contract_id, descriptions):
        report = ""
        if action['params'].get('send_report'):
            report = '<br><br><strong>События:</strong><ul>' + ''.join(
                ["<li>{}</li>".format(description) for description in descriptions]) + "</ul>"

        if action['type'] == 'patient_message':
            self.medsenger_api.send_message(contract_id, action['params']['text'] + report,
                                            only_patient=True,
                                            is_urgent=action['params'].get('is_urgent'))
        if action['type'] == 'doctor_message':
            self.medsenger_api.send_message(contract_id, action['params']['text'] + report,
                                            only_doctor=True,
                                            is_urgent=action['params'].get('is_urgent'),
                                            need_answer=action['params'].get('need_answer'))
        if action['type'] == 'record':
            category_name = action['params'].get('category')
            value = action['params'].get('value')

            self.medsenger_api.add_record(contract_id, category_name, value)

        if action['type'] == 'medicine':
            name = action['params'].get('medicine_name')
            rules = action['params'].get('medicine_rules')

            self.medsenger_api.send_message(contract_id,
                                            'Внимание! В соответствие с алгоритмом, Вам требуется дополнительное принять препарат {}.<br>Комментарий: {}.'.format(
                                                name, rules), only_patient=True,
                                            is_urgent=True)
            self.medsenger_api.send_message(contract_id,
                                            'Внимание! В соответствие с алгоритмом, пациенту отправлена просьба принять препарат {}.<br>Комментарий: {}.'.format(
                                                name, rules), only_doctor=True,
                                            is_urgent=True)
        if action['type'] in ['form', 'attach_form', 'detach_form', 'attach_algorithm', 'detach_algorithm',
                              'attach_medicine', 'detach_medicine']:
            form_manager = FormManager(self.medsenger_api, self.db)
            contract_manager = ContractManager(self.medsenger_api, self.db)
            medicine_manager = MedicineManager(self.medsenger_api, self.db)

            contract = contract_manager.get(contract_id)
            template_id = action['params'].get('template_id')

            if action['type'] == 'form':
                form = form_manager.get(template_id)

                if form:
                    form_manager.run(form, False, contract_id)

            if action['type'] == 'attach_form':
                form = form_manager.get(template_id)

                if form:
                    form_manager.attach(template_id, contract)
                    self.medsenger_api.send_message(contract_id,
                                                    'Опросник {} автоматически подключен.'.format(form.title),
                                                    only_doctor=True)

            if action['type'] == 'detach_form':
                form = form_manager.get(template_id)

                if form:
                    form_manager.detach(template_id, contract)
                    self.medsenger_api.send_message(contract_id,
                                                    'Опросник {} автоматически отключен.'.format(form.title),
                                                    only_doctor=True)

            if action['type'] == 'attach_algorithm':
                algorithm = self.get(template_id)

                if algorithm:
                    self.attach(template_id, contract)
                    self.medsenger_api.send_message(contract_id,
                                                    'Алгоритм {} автоматически подключен.'.format(algorithm.title),
                                                    only_doctor=True)

            if action['type'] == 'detach_algorithm':
                algorithm = self.get(template_id)

                if algorithm:
                    self.detach(template_id, contract)
                    self.medsenger_api.send_message(contract_id,
                                                    'Алгоритм {} автоматически отключен.'.format(algorithm.title),
                                                    only_doctor=True)

            if action['type'] == 'attach_medicine':
                medicine = self.get(template_id)

                if medicine:
                    medicine_manager.attach(template_id, contract)
                    self.medsenger_api.send_message(contract_id, 'Вам назначен препарат {} ({} / {}).'.format(
                        medicine.title, medicine.rules, medicine.timetable_description()),
                                                    only_patient=True)
                    self.medsenger_api.send_message(contract_id,
                                                    'Внимание! Препарат {} ({} / {}) назначен автоматически.'.format(
                                                        medicine.title, medicine.rules,
                                                        medicine.timetable_description()),
                                                    only_doctor=True)

            if action['type'] == 'detach_medicine':
                medicine = self.get(template_id)

                if medicine:
                    medicine_manager.detach(template_id, contract)

                    self.medsenger_api.send_message(contract_id, 'Препарат {} ({} / {}) отменен.'.format(
                        medicine.title, medicine.rules, medicine.timetable_description()),
                                                    only_patient=True)
                    self.medsenger_api.send_message(contract_id,
                                                    'Внимание! Препарат {} ({} / {}) отменен автоматически.'.format(
                                                        medicine.title, medicine.rules,
                                                        medicine.timetable_description()),
                                                    only_doctor=True)

    def run(self, algorithm):
        criteria = algorithm.criteria
        actions = algorithm.actions
        contract_id = algorithm.contract_id

        additions = []
        descriptions = []
        category_names = {category['name']: category['description'] for category in self.medsenger_api.get_categories()}

        result = any([all(
            list(map(lambda x: self.check_criteria(x, contract_id, additions, descriptions, category_names), block)))
            for block in criteria])

        if result:
            for addition in additions:
                self.medsenger_api.send_addition(contract_id, addition['id'], {
                    "algorithm_id": algorithm.id,
                    "comment": addition["comment"]
                })

            for action in actions:
                self.run_action(action, contract_id, descriptions)

    def examine(self, contract, form):
        categories = form.categories.split('|')
        patient = contract.patient

        algorithms = list(filter(lambda algorithm: any([cat in algorithm.categories.split('|') for cat in categories]),
                                 patient.algorithms))

        for algorithm in algorithms:
            self.run(algorithm)

    def hook(self, contract, category_name):
        patient = contract.patient

        algorithms = list(
            filter(lambda algorithm: category_name in algorithm.categories.split('|'), patient.algorithms))

        for algorithm in algorithms:
            self.run(algorithm)

        return True

    def create_or_edit(self, data, contract):
        try:
            algorithm_id = data.get('id')
            if not algorithm_id:
                algorithm = Algorithm()
            else:
                algorithm = Algorithm.query.filter_by(id=algorithm_id).first_or_404()

                if algorithm.contract_id != contract.id and not contract.is_admin:
                    return None

            algorithm.title = data.get('title')
            algorithm.criteria = data.get('criteria')
            algorithm.actions = data.get('actions')
            algorithm.description = data.get('description')
            algorithm.categories = data.get('categories')
            algorithm.template_id = data.get('template_id')

            if data.get('is_template'):
                algorithm.is_template = True
                algorithm.template_category = data.get('template_category')
            else:
                algorithm.patient_id = contract.patient_id
                algorithm.contract_id = contract.id

            if not algorithm_id:
                self.db.session.add(algorithm)
            self.__commit__()

            return algorithm
        except Exception as e:
            log(e)
            return None
