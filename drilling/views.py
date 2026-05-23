import hashlib
import os
import pandas as pd
import numpy as np
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from fpdf import FPDF
from django.utils.timezone import now

from .models import CustomUser, Project, Fluid, CalculationHistory, Reagent, FluidRecipe, LocalWeight, BaseWeight
from .services import calculate_physics, calculate_topsis, get_recipe_data, aggregate_and_normalize_weights, \
    get_calculated_properties, calculate_ahp_weights


def custom_login_required(view_func):
    def wrapper(request, *args, **kwargs):
        if 'user_id' not in request.session: return redirect('login')
        return view_func(request, *args, **kwargs)

    return wrapper


def login_view(request):
    error = None
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        hashed_p = hashlib.sha256(p.encode()).hexdigest()
        try:
            user = CustomUser.objects.get(username=u, password_hash=hashed_p)
            request.session['user_id'] = user.id
            request.session['username'] = user.username
            request.session['role'] = user.role
            return redirect('dashboard')
        except CustomUser.DoesNotExist:
            error = "Неверный логин или пароль"
    return render(request, 'drilling/login.html', {'error': error})


def logout_view(request):
    request.session.flush()
    return redirect('login')


@custom_login_required
def dashboard(request):
    user = CustomUser.objects.get(id=request.session['user_id'])
    if request.method == 'POST' and 'new_project' in request.POST:
        Project.objects.create(user=user, name=request.POST.get('project_name'), created_at=now())
        return redirect('dashboard')
    projects = Project.objects.filter(user=user).order_by('-created_at')
    return render(request, 'drilling/dashboard.html',
                  {'projects': projects, 'username': user.username, 'role': user.role})


@custom_login_required
def project_detail(request, pk):
    user = CustomUser.objects.get(id=request.session['user_id'])
    project = get_object_or_404(Project, pk=pk, user=user)
    intervals_db = CalculationHistory.objects.filter(project=project).order_by('id')

    interval_data = []

    for it in intervals_db:
        V, recipe, cost_total, cost_m3 = get_recipe_data(it.selected_fluid.id, it.req_density_min, it.diameter,
                                                         it.depth, it.t_zab, it.complications_list or "")

        for r in recipe:
            try:
                r['price_kg'] = Reagent.objects.get(name=r['name']).price_kg
            except Reagent.DoesNotExist:
                r['price_kg'] = 0.0

        is_gas_or_unexplored = 'Газ' in (it.complications_list or "")
        _, rho_max, dp_sigma, p_g, p_pogl, p_gr, _ = calculate_physics(it.depth, it.diameter, it.p_pl,
                                                                       is_gas_or_unexplored)
        target_props = get_calculated_properties(it.req_density_min, it.angle, dp_sigma)
        is_productive = 'Продуктивный' in it.interval_type

        fluids = Fluid.objects.filter(temp_max__gte=it.t_zab, density_max__gte=it.req_density_min,
                                      density_min__lte=rho_max, is_drill_in_fluid=is_productive)
        alternatives = []

        if fluids.exists():
            weights = {'weight_filtration': it.final_w_filtration, 'weight_inhibition': it.final_w_inhibition,
                       'weight_friction': it.final_w_friction, 'weight_eco': it.final_w_eco,
                       'weight_cost': it.final_w_cost}
            fluid_list = []
            for f in fluids:
                f_dict = f.__dict__.copy()
                _, _, _, dyn_cost_m3 = get_recipe_data(f.id, it.req_density_min, it.diameter, it.depth, it.t_zab,
                                                       it.complications_list or "")
                f_dict['cost'] = dyn_cost_m3
                fluid_list.append(f_dict)

            df = pd.DataFrame(fluid_list)
            df['rating'] = calculate_topsis(df, weights)
            best_cost = df.sort_values('rating', ascending=False).iloc[0]['cost']

            place = 2
            for _, row in df.sort_values('rating', ascending=False).head(3).iloc[1:].iterrows():
                reasons = []
                if row['cost'] > best_cost * 1.05: reasons.append(f"дороже на {int(row['cost'] - best_cost)} руб/м³")
                if row['eco_score'] < df.sort_values('rating', ascending=False).iloc[0]['eco_score']: reasons.append(
                    "ниже экологичность")
                if row['filtration'] > df.sort_values('rating', ascending=False).iloc[0]['filtration']: reasons.append(
                    "выше водоотдача")

                reason_str = ", ".join(reasons[:2]) or "незначительное отставание по весам TOPSIS"
                _, alt_recipe, alt_total_cost, _ = get_recipe_data(int(row['id']), it.req_density_min, it.diameter,
                                                                   it.depth, it.t_zab, it.complications_list or "")

                for r in alt_recipe:
                    try:
                        r['price_kg'] = Reagent.objects.get(name=r['name']).price_kg
                    except:
                        r['price_kg'] = 0.0

                alternatives.append({
                    'place': place, 'name': row['name'], 'base': row['base_type'],
                    'rating': round(row['rating'] * 100, 1),
                    'cost': round(row['cost'], 0), 'total_cost': round(alt_total_cost, 0), 'recipe': alt_recipe,
                    'd_min': row['density_min'], 'd_max': row['density_max'], 't_max': row['temp_max'],
                    'filtration': row['filtration'], 'inhibition': row['inhibition'], 'reason': reason_str.capitalize()
                })
                place += 1

        interval_data.append(
            {'obj': it, 'volume': V, 'recipe': recipe, 'cost': cost_total, 'alternatives': alternatives,
             'p_pogl': p_pogl, 'p_gr': p_gr, 'target_props': target_props})

    error_msg = None
    form_data = {'interval_name': '', 'depth': '', 'diameter': '', 'p_pl': '', 't_zab': '', 'angle': '0',
                 'interval_type': '', 'well_profile': '', 'fluid_type': '', 'complications': []}

    if request.method == 'POST':
        form_data = {
            'interval_name': request.POST.get('interval_name', ''), 'depth': request.POST.get('depth', ''),
            'diameter': request.POST.get('diameter', ''), 'p_pl': request.POST.get('p_pl', ''),
            't_zab': request.POST.get('t_zab', ''), 'angle': request.POST.get('angle', '0'),
            'interval_type': request.POST.get('interval_type', ''),
            'well_profile': request.POST.get('well_profile', ''),
            'fluid_type': request.POST.get('fluid_type', ''), 'complications': request.POST.getlist('complications')
        }

        H, D, P_pl, T_zab, angle = float(form_data['depth'] or 0.0), float(
            form_data['diameter'] if form_data['diameter'].strip() else 0.0), float(form_data['p_pl'] or 0.0), float(
            form_data['t_zab'] or 0.0), float(form_data['angle'] or 0.0)
        complications = form_data['complications']
        if form_data['fluid_type'] == 'Газ' and 'Газ' not in complications: complications.append('Газ')

        rho_min, rho_max, dp_sigma, p_g, p_pogl, p_gr, err = calculate_physics(H, D, P_pl,
                                                                               form_data['fluid_type'] == 'Газ')

        if err:
            error_msg = err
        else:
            fluids = Fluid.objects.filter(temp_max__gte=T_zab, density_max__gte=rho_min, density_min__lte=rho_max,
                                          is_drill_in_fluid=('Продуктивный' in form_data['interval_type']))
            if fluids.exists():
                final_weights = aggregate_and_normalize_weights(form_data['interval_type'], form_data['well_profile'],
                                                                form_data['fluid_type'], complications)
                fluid_list = []
                comp_str = ", ".join(complications) if complications else "Нет осложнений"
                for f in fluids:
                    f_dict = f.__dict__.copy()
                    _, _, _, dyn_cost_m3 = get_recipe_data(f.id, rho_min, D, H, T_zab, comp_str)
                    f_dict['cost'] = dyn_cost_m3
                    fluid_list.append(f_dict)

                df = pd.DataFrame(fluid_list)
                df['rating'] = calculate_topsis(df, final_weights)
                best_fluid = Fluid.objects.get(id=int(df.sort_values('rating', ascending=False).iloc[0]['id']))

                CalculationHistory.objects.create(
                    project=project, user=user, interval_name=form_data['interval_name'], depth=H, diameter=D,
                    t_zab=T_zab, angle=angle, p_pl=P_pl,
                    req_density_min=rho_min, req_density_max=rho_max, interval_type=form_data['interval_type'],
                    well_profile=form_data['well_profile'], complications_list=comp_str,
                    final_w_filtration=final_weights['weight_filtration'],
                    final_w_inhibition=final_weights['weight_inhibition'],
                    final_w_friction=final_weights['weight_friction'],
                    final_w_eco=final_weights['weight_eco'], final_w_cost=final_weights['weight_cost'],
                    selected_fluid=best_fluid, calc_date=now()
                )
                return redirect('project_detail', pk=pk)
            else:
                error_msg = f"Нет растворов под эти условия (T>{T_zab}°C, Плотн:{rho_min}-{rho_max})"

    return render(request, 'drilling/project.html',
                  {'project': project, 'intervals': interval_data, 'error_msg': error_msg, 'form_data': form_data,
                   'username': user.username, 'role': user.role})


@custom_login_required
def delete_interval(request, pk, proj_id):
    CalculationHistory.objects.filter(pk=pk).delete()
    return redirect('project_detail', pk=proj_id)


@custom_login_required
def export_pdf(request, pk):
    user = CustomUser.objects.get(id=request.session['user_id'])
    project = get_object_or_404(Project, pk=pk, user=user)
    intervals_db = CalculationHistory.objects.filter(project=project).order_by('id')

    pdf = FPDF()
    font_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Roboto-Regular.ttf')

    def safe_text(text, max_w):
        text = str(text)
        if pdf.get_string_width(text) <= max_w: return text
        while len(text) > 0 and pdf.get_string_width(text + "...") > max_w: text = text[:-1]
        return text + "..."

    for it in intervals_db:
        pdf.add_page()
        if os.path.exists(font_path):
            pdf.add_font('Roboto', '', font_path, uni=True); pdf.set_font('Roboto', size=11)
        else:
            pdf.set_font('Helvetica', size=11)

        V, recipe, cost, _ = get_recipe_data(it.selected_fluid.id, it.req_density_min, it.diameter, it.depth, it.t_zab,
                                             it.complications_list or "")
        is_gas = 'Газ' in (it.complications_list or "")
        _, rho_max, dp_sigma, p_g, p_pogl, p_gr, _ = calculate_physics(it.depth, it.diameter, it.p_pl, is_gas)
        target_props = get_calculated_properties(it.req_density_min, it.angle, dp_sigma)

        # ШАПКА
        pdf.set_font(pdf.font_family, size=16)
        pdf.cell(0, 10, txt="ПРОГРАММА ПРОМЫВКИ СКВАЖИНЫ", ln=True, align='C')
        pdf.ln(5)
        pdf.set_font(pdf.font_family, size=11)
        pdf.cell(100, 6, txt=safe_text(f"Проект: {project.name}", 98))
        pdf.cell(90, 6, txt=f"Дата: {now().strftime('%d.%m.%Y')}", ln=True)
        pdf.cell(100, 6, txt=safe_text(f"Инженер: {user.full_name}", 98))
        pdf.line(10, pdf.get_y() + 2, 200, pdf.get_y() + 2)
        pdf.ln(8)

        # ЗАГОЛОВОК ИНТЕРВАЛА
        pdf.set_font(pdf.font_family, size=14)
        pdf.cell(0, 8, txt=safe_text(f"ИНТЕРВАЛ: {it.interval_name} (до {it.depth} м)", 190), ln=True)
        pdf.set_font(pdf.font_family, size=12)
        pdf.cell(0, 7, txt=safe_text(f"1. {it.selected_fluid.name}", 190), ln=True)
        pdf.cell(0, 7, txt=f"Объем: {V} м3 | Стоимость: {cost:,.0f} руб.", ln=True)
        pdf.ln(5)

        # 3 БЛОКА КАК В ИНТЕРФЕЙСЕ (ВЕРТИКАЛЬНО ДЛЯ НАДЕЖНОСТИ В PDF)
        pdf.set_font(pdf.font_family, size=11)

        pdf.cell(0, 6, txt="ИСХОДНЫЕ ДАННЫЕ:", ln=True)
        pdf.set_font(pdf.font_family, size=10)
        pdf.cell(0, 5, txt=f"Диаметр долота: {it.diameter} мм | P пласта: {it.p_pl} МПа | T забоя: {it.t_zab} C",
                 ln=True)
        pdf.cell(0, 5,
                 txt=f"Профиль: {it.well_profile} ({it.angle} град.) | Осложнения: {it.complications_list or 'Нет'}",
                 ln=True)
        pdf.ln(4)

        pdf.set_font(pdf.font_family, size=11)
        pdf.cell(0, 6, txt="РЕЗУЛЬТАТ ПОДБОРА:", ln=True)
        pdf.set_font(pdf.font_family, size=10)
        pdf.cell(0, 5,
                 txt=f"Основа: {it.selected_fluid.base_type} | Плотность: {it.selected_fluid.density_min}-{it.selected_fluid.density_max} г/см3 | T макс: {it.selected_fluid.temp_max} C",
                 ln=True)
        pdf.cell(0, 5,
                 txt=f"Водоотдача: {it.selected_fluid.filtration} см3 | Ингибирование: {it.selected_fluid.inhibition} у.е.",
                 ln=True)
        pdf.ln(4)

        pdf.set_font(pdf.font_family, size=11)
        pdf.cell(0, 6, txt="РАСЧЕТ ПАРАМЕТРОВ:", ln=True)
        pdf.set_font(pdf.font_family, size=10)
        pdf.cell(0, 5, txt=f"P погл: {p_pogl} МПа | P гр: {p_gr} МПа | Тр. плотность: {it.req_density_min} г/см3",
                 ln=True)
        pdf.cell(0, 5,
                 txt=f"Пл. вязкость: {target_props['viscosity']} мПа*с | ДНС: {target_props['tau_0']} дПа | Макс. водоотдача: {target_props['filtration']} см3",
                 ln=True)
        pdf.ln(6)

        # РЕЦЕПТУРА
        pdf.set_font(pdf.font_family, size=11)
        pdf.cell(0, 6, txt="Детализация рецептуры:", ln=True)
        pdf.set_font(pdf.font_family, size=9)
        pdf.cell(70, 7, txt="Наименование", border=1, fill=True)
        pdf.cell(60, 7, txt="Назначение", border=1, fill=True)
        pdf.cell(30, 7, txt="Конц. (кг/м3)", border=1, align='C', fill=True)
        pdf.cell(30, 7, txt="Цена (руб/кг)", border=1, ln=True, align='C', fill=True)

        for r in recipe:
            try:
                price_kg = Reagent.objects.get(name=r['name']).price_kg
            except:
                price_kg = 0.0

            pdf.cell(70, 7, txt=safe_text(r['name'], 68), border=1)
            pdf.cell(60, 7, txt=safe_text(r['func'], 58), border=1)
            pdf.cell(30, 7, txt=str(r['mass']), border=1, align='C')
            pdf.cell(30, 7, txt=str(price_kg), border=1, ln=True, align='C')

    pdf_bytes = pdf.output()
    response = HttpResponse(bytes(pdf_bytes), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Mud_Program_{project.id}.pdf"'
    return response


@custom_login_required
def admin_panel(request):
    role = request.session.get('role')
    # Доступ разрешен и админу, и главному инженеру
    if role not in ['admin', 'main_engineer']:
        return redirect('dashboard')

    if request.method == 'POST':
        # Проверка прав: Админ может только работать с пользователями
        if role == 'admin' and 'add_user' in request.POST:
            u = request.POST.get('new_username')
            p = request.POST.get('new_password')
            r = request.POST.get('new_role')  # Роль теперь может быть любой строкой
            hashed_p = hashlib.sha256(p.encode()).hexdigest()
            CustomUser.objects.create(username=u, password_hash=hashed_p, role=r)

        elif role == 'admin' and 'delete_user' in request.POST:
            CustomUser.objects.filter(id=request.POST.get('delete_user')).delete()

        # Права Главного инженера: Растворы, Реагенты, Рецептуры, МАИ
        elif role == 'main_engineer':
            if 'add_fluid' in request.POST:
                Fluid.objects.create(name=request.POST.get('f_name'), base_type=request.POST.get('f_base'),
                                     density_min=request.POST.get('f_d_min'), density_max=request.POST.get('f_d_max'),
                                     temp_max=request.POST.get('f_t_max'), inhibition=request.POST.get('f_inh'),
                                     friction=request.POST.get('f_fric'), eco_score=request.POST.get('f_eco'),
                                     cost=request.POST.get('f_cost'), filtration=6.0, base_density=1.05,
                                     is_drill_in_fluid=(request.POST.get('f_base') == 'Продуктивный'))
            elif 'add_reagent' in request.POST:
                Reagent.objects.create(name=request.POST.get('r_name'), function_type=request.POST.get('r_func'),
                                       target_lithology=request.POST.get('r_litho'),
                                       max_temp=request.POST.get('r_temp'), price_kg=float(request.POST.get('r_price')))
            elif 'add_recipe' in request.POST:
                FluidRecipe.objects.create(fluid=Fluid.objects.get(id=request.POST.get('recipe_fluid')),
                                           reagent=Reagent.objects.get(id=request.POST.get('recipe_reagent')),
                                           concentration=request.POST.get('recipe_conc'),
                                           comment=request.POST.get('recipe_comment'))

            # Стандартное МАИ (5x5)
            elif 'update_ahp_preset' in request.POST:
                p_id = request.POST.get('preset_selector')
                preset = LocalWeight.objects.get(id=p_id)

                def parse_saaty(val_name):
                    val = request.POST.get(val_name, '1');
                    return float(val.split('/')[0]) / float(val.split('/')[1]) if '/' in val else float(val)

                m01, m02, m03, m04 = parse_saaty('p_0_1'), parse_saaty('p_0_2'), parse_saaty('p_0_3'), parse_saaty(
                    'p_0_4')
                m12, m13, m14 = parse_saaty('p_1_2'), parse_saaty('p_1_3'), parse_saaty('p_1_4')
                m23, m24, m34 = parse_saaty('p_2_3'), parse_saaty('p_2_4'), parse_saaty('p_3_4')

                matrix = np.array(
                    [[1.0, m01, m02, m03, m04], [1.0 / m01, 1.0, m12, m13, m14], [1.0 / m02, 1.0 / m12, 1.0, m23, m24],
                     [1.0 / m03, 1.0 / m13, 1.0 / m23, 1.0, m34], [1.0 / m04, 1.0 / m14, 1.0 / m24, 1.0 / m34, 1.0]])
                weights, cr = calculate_ahp_weights(matrix)

                if cr > 0.1:
                    request.session['ahp_msg'] = f"ОШИБКА: Матрица противоречива (CR = {cr})."; request.session[
                        'ahp_status'] = "danger"
                else:
                    request.session['ahp_msg'] = f"УСПЕХ: Веса сохранены! CR = {cr}";
                    request.session['ahp_status'] = "success"
                    preset.weight_cost, preset.weight_filtration, preset.weight_inhibition, preset.weight_friction, preset.weight_eco = weights
                    preset.save()

            # МАИ для МЕТА-ВЕСОВ (3x3)
            elif 'update_meta_ahp' in request.POST:
                def parse_saaty(val_name):
                    val = request.POST.get(val_name, '1');
                    return float(val.split('/')[0]) / float(val.split('/')[1]) if '/' in val else float(val)

                m01 = parse_saaty('m_0_1')  # Интервал vs Профиль
                m02 = parse_saaty('m_0_2')  # Интервал vs Осложнения
                m12 = parse_saaty('m_1_2')  # Профиль vs Осложнения

                matrix = np.array([[1.0, m01, m02], [1.0 / m01, 1.0, m12], [1.0 / m02, 1.0 / m12, 1.0]])
                weights, cr = calculate_ahp_weights(matrix)

                if cr > 0.1:
                    request.session['ahp_msg'] = f"ОШИБКА МЕТА-ВЕСОВ: CR = {cr}."; request.session[
                        'ahp_status'] = "danger"
                else:
                    request.session[
                        'ahp_msg'] = f"МЕТА-ВЕСА СОХРАНЕНЫ! Интервал: {weights[0]}, Профиль: {weights[1]}, Осложн: {weights[2]}";
                    request.session['ahp_status'] = "success"
                    base = BaseWeight.objects.first()
                    if not base: meta = BaseWeight()
                    base.weight_interval, base.weight_profile, base.weight_complication = weights
                    base.save()

            elif 'delete_fluid' in request.POST:
                Fluid.objects.filter(id=request.POST.get('delete_fluid')).delete()
            elif 'delete_reagent' in request.POST:
                Reagent.objects.filter(id=request.POST.get('delete_reagent')).delete()
            elif 'delete_recipe' in request.POST:
                FluidRecipe.objects.filter(id=request.POST.get('delete_recipe')).delete()

        return redirect('admin_panel')

    context = {
        'fluids': Fluid.objects.all().order_by('name'), 'reagents': Reagent.objects.all().order_by('name'),
        'users': CustomUser.objects.all().order_by('-id'), 'recipes': FluidRecipe.objects.all().order_by('-id'),
        'interval_presets': LocalWeight.objects.filter(category='interval').order_by('id'),
        'profile_presets': LocalWeight.objects.filter(category='profile').order_by('id'),
        'comp_presets': LocalWeight.objects.filter(category='complication').order_by('id'),
        'meta_weight': BaseWeight.objects.first(),
        'history': CalculationHistory.objects.all().select_related('user', 'project', 'selected_fluid').order_by(
            '-calc_date')[:50],
        'username': request.session.get('username'), 'role': role
    }
    context['ahp_msg'], context['ahp_status'] = request.session.pop('ahp_msg', None), request.session.pop('ahp_status',
                                                                                                          None)

    return render(request, 'drilling/admin.html', context)