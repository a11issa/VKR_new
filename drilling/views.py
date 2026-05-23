import hashlib
import os
import pandas as pd
import numpy as np
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse
from fpdf import FPDF
from django.utils.timezone import now

# Импортируем модели и сервисы
from .models import CustomUser, Project, Fluid, CalculationHistory, Reagent, FluidRecipe, LocalWeight
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
    return render(request, 'drilling/dashboard.html', {'projects': projects, 'username': user.username})


@custom_login_required
def project_detail(request, pk):
    user = CustomUser.objects.get(id=request.session['user_id'])
    project = get_object_or_404(Project, pk=pk, user=user)
    intervals_db = CalculationHistory.objects.filter(project=project).order_by('id')

    interval_data = []

    for it in intervals_db:
        # Расчет для основного решения (Первое место)
        V, recipe, cost_total, cost_m3 = get_recipe_data(
            it.selected_fluid.id, it.req_density_min, it.diameter,
            it.depth, it.t_zab, it.complications_list or ""
        )

        # Получаем чистую цену за 1 кг из базы для основного рецепта
        for r in recipe:
            try:
                reagent_obj = Reagent.objects.get(name=r['name'])
                r['price_kg'] = reagent_obj.price_kg
            except Reagent.DoesNotExist:
                r['price_kg'] = 0.0

        is_gas_or_unexplored = 'Газ' in (it.complications_list or "")
        _, rho_max, dp_sigma, p_g, p_pogl, p_gr, _ = calculate_physics(
            it.depth, it.diameter, it.p_pl, is_gas_or_unexplored
        )

        target_props = get_calculated_properties(it.req_density_min, it.angle, dp_sigma)
        is_productive = 'Продуктивный' in it.interval_type

        fluids = Fluid.objects.filter(
            temp_max__gte=it.t_zab, density_max__gte=it.req_density_min,
            density_min__lte=rho_max, is_drill_in_fluid=is_productive
        )

        alternatives = []
        if fluids.exists():
            weights = {
                'weight_filtration': it.final_w_filtration, 'weight_inhibition': it.final_w_inhibition,
                'weight_friction': it.final_w_friction, 'weight_eco': it.final_w_eco, 'weight_cost': it.final_w_cost
            }

            fluid_list = []
            for f in fluids:
                f_dict = f.__dict__.copy()
                _, _, _, dyn_cost_m3 = get_recipe_data(
                    f.id, it.req_density_min, it.diameter, it.depth, it.t_zab,
                    it.complications_list or ""
                )
                f_dict['cost'] = dyn_cost_m3
                fluid_list.append(f_dict)

            df = pd.DataFrame(fluid_list)
            df['rating'] = calculate_topsis(df, weights)

            best_row = df.sort_values('rating', ascending=False).iloc[0]
            best_cost = best_row['cost']

            top3_df = df.sort_values('rating', ascending=False).head(3).iloc[1:]

            place = 2
            for _, row in top3_df.iterrows():
                reasons = []
                if row['cost'] > best_cost * 1.05:
                    reasons.append(f"дороже на {int(row['cost'] - best_cost)} руб/м³")
                if row['eco_score'] < best_row['eco_score']:
                    reasons.append("ниже экологичность")
                if row['filtration'] > best_row['filtration']:
                    reasons.append("выше водоотдача")
                if row['inhibition'] < best_row['inhibition']:
                    reasons.append("хуже ингибирование")

                reason_str = ", ".join(reasons[:2])
                if not reason_str:
                    reason_str = "незначительное отставание по весам TOPSIS"

                # Расчет рецептуры и полной сметы для альтернативных вариантов
                alt_id = int(row['id'])
                _, alt_recipe, alt_total_cost, _ = get_recipe_data(
                    alt_id, it.req_density_min, it.diameter, it.depth, it.t_zab, it.complications_list or ""
                )

                # Получаем чистую цену за 1 кг из базы для альтернативного рецепта
                for r in alt_recipe:
                    try:
                        reagent_obj = Reagent.objects.get(name=r['name'])
                        r['price_kg'] = reagent_obj.price_kg
                    except Reagent.DoesNotExist:
                        r['price_kg'] = 0.0

                alternatives.append({
                    'place': place,
                    'name': row['name'],
                    'base': row['base_type'],
                    'rating': round(row['rating'] * 100, 1),
                    'cost': round(row['cost'], 0),
                    'total_cost': round(alt_total_cost, 0),
                    'recipe': alt_recipe,
                    'd_min': row['density_min'],
                    'd_max': row['density_max'],
                    't_max': row['temp_max'],
                    'filtration': row['filtration'],
                    'inhibition': row['inhibition'],
                    'reason': reason_str.capitalize()
                })
                place += 1

        interval_data.append({
            'obj': it, 'volume': V, 'recipe': recipe, 'cost': cost_total, 'alternatives': alternatives,
            'p_pogl': p_pogl, 'p_gr': p_gr, 'target_props': target_props
        })

    error_msg = None

    # Дефолтное значение диаметра 215.9 убрано из инициализации
    form_data = {
        'interval_name': '', 'depth': '', 'diameter': '',
        'p_pl': '', 't_zab': '', 'angle': '0',
        'interval_type': '', 'well_profile': '', 'fluid_type': '',
        'complications': []
    }

    if request.method == 'POST':
        form_data = {
            'interval_name': request.POST.get('interval_name', ''),
            'depth': request.POST.get('depth', ''),
            'diameter': request.POST.get('diameter', ''),
            'p_pl': request.POST.get('p_pl', ''),
            't_zab': request.POST.get('t_zab', ''),
            'angle': request.POST.get('angle', '0'),
            'interval_type': request.POST.get('interval_type', ''),
            'well_profile': request.POST.get('well_profile', ''),
            'fluid_type': request.POST.get('fluid_type', ''),
            'complications': request.POST.getlist('complications')
        }

        interval_name = form_data['interval_name']
        H = float(form_data['depth'] or 0.0)

        # Если поле осталось пустым, во избежание сбоя берем 0.0
        D_str = form_data['diameter']
        D = float(D_str) if D_str.strip() else 0.0

        P_pl = float(form_data['p_pl'] or 0.0)
        T_zab = float(form_data['t_zab'] or 0.0)
        angle = float(form_data['angle'] or 0.0)

        interval_type = form_data['interval_type']
        well_profile = form_data['well_profile']
        fluid_type = form_data['fluid_type']

        complications = form_data['complications']
        if fluid_type == 'Газ' and 'Газ' not in complications:
            complications.append('Газ')

        comp_str = ", ".join(complications) if complications else "Нет осложнений"

        is_gas = (fluid_type == 'Газ')
        rho_min, rho_max, dp_sigma, p_g, p_pogl, p_gr, err = calculate_physics(H, D, P_pl, is_gas)

        if err:
            error_msg = err
        else:
            is_productive = 'Продуктивный' in interval_type

            fluids = Fluid.objects.filter(
                temp_max__gte=T_zab,
                density_max__gte=rho_min,
                density_min__lte=rho_max,
                is_drill_in_fluid=is_productive
            )

            if fluids.exists():
                final_weights = aggregate_and_normalize_weights(interval_type, well_profile, fluid_type, complications)

                fluid_list = []
                for f in fluids:
                    f_dict = f.__dict__.copy()
                    _, _, _, dyn_cost_m3 = get_recipe_data(f.id, rho_min, D, H, T_zab, comp_str)
                    f_dict['cost'] = dyn_cost_m3
                    fluid_list.append(f_dict)

                df = pd.DataFrame(fluid_list)
                df['rating'] = calculate_topsis(df, final_weights)
                best_fluid_id = int(df.sort_values('rating', ascending=False).iloc[0]['id'])
                best_fluid = Fluid.objects.get(id=best_fluid_id)

                CalculationHistory.objects.create(
                    project=project, user=user, interval_name=interval_name, depth=H, diameter=D, t_zab=T_zab,
                    angle=angle, p_pl=P_pl,
                    req_density_min=rho_min, req_density_max=rho_max, interval_type=interval_type,
                    well_profile=well_profile, complications_list=comp_str,
                    final_w_filtration=final_weights['weight_filtration'],
                    final_w_inhibition=final_weights['weight_inhibition'],
                    final_w_friction=final_weights['weight_friction'], final_w_eco=final_weights['weight_eco'],
                    final_w_cost=final_weights['weight_cost'], selected_fluid=best_fluid, calc_date=now()
                )
                return redirect('project_detail', pk=pk)
            else:
                error_msg = f"Нет растворов под эти условия (T>{T_zab}°C, Плотн:{rho_min}-{rho_max})"

    return render(request, 'drilling/project.html',
                  {'project': project, 'intervals': interval_data, 'error_msg': error_msg,
                   'form_data': form_data, 'username': user.username})


@custom_login_required
def delete_interval(request, pk, proj_id):
    CalculationHistory.objects.filter(pk=pk).delete()
    return redirect('project_detail', pk=proj_id)


# Оставшаяся часть файла (export_pdf, admin_panel) остается без изменений


# =================================================================================
# ФУНКЦИЯ ГЕНЕРАЦИИ PDF
# =================================================================================
@custom_login_required
def export_pdf(request, pk):
    user = CustomUser.objects.get(id=request.session['user_id'])
    project = get_object_or_404(Project, pk=pk, user=user)
    intervals_db = CalculationHistory.objects.filter(project=project).order_by('id')

    pdf = FPDF()
    current_dir = os.path.dirname(os.path.abspath(__file__))
    font_path = os.path.join(current_dir, 'Roboto-Regular.ttf')

    pdf.add_page()

    if os.path.exists(font_path):
        pdf.add_font('Roboto', '', font_path, uni=True)
        pdf.set_font('Roboto', size=11)
    else:
        pdf.set_font('Helvetica', size=11)

    def safe_text(text, max_w):
        text = str(text)
        if pdf.get_string_width(text) <= max_w:
            return text
        while len(text) > 0 and pdf.get_string_width(text + "...") > max_w:
            text = text[:-1]
        return text + "..."

    pdf.set_font(pdf.font_family, size=16)
    pdf.cell(0, 10, txt="ПРОГРАММА ПРОМЫВКИ СКВАЖИНЫ", ln=True, align='C')
    pdf.ln(5)

    pdf.set_font(pdf.font_family, size=11)
    pdf.cell(100, 6, txt=safe_text(f"Месторождение / Скважина: {project.name}", 98))
    pdf.cell(90, 6, txt=f"Дата расчета: {now().strftime('%d.%m.%Y')}", ln=True)

    pdf.cell(100, 6, txt=safe_text(f"Организация: {user.company}", 98))
    pdf.cell(90, 6, txt="Документ: Технологический регламент", ln=True)

    pdf.line(10, pdf.get_y() + 2, 200, pdf.get_y() + 2)
    pdf.ln(8)

    for it in intervals_db:
        V, recipe, cost, _ = get_recipe_data(it.selected_fluid.id, it.req_density_min, it.diameter, it.depth, it.t_zab,
                                             it.complications_list or "")
        is_gas_or_unexplored = 'Газ' in (it.complications_list or "")
        _, rho_max, dp_sigma, p_g, p_pogl, p_gr, _ = calculate_physics(it.depth, it.diameter, it.p_pl,
                                                                       is_gas_or_unexplored)
        target_props = get_calculated_properties(it.req_density_min, it.angle, dp_sigma)

        if pdf.get_y() > 240:
            pdf.add_page()

        pdf.set_font(pdf.font_family, size=14)
        pdf.cell(0, 8, txt=safe_text(f"ИНТЕРВАЛ БУРЕНИЯ: {it.interval_name} (до {it.depth} м)", 190), ln=True)

        pdf.set_font(pdf.font_family, size=12)
        pdf.cell(0, 7, txt=safe_text(f"РЕКОМЕНДУЕМАЯ СИСТЕМА: {it.selected_fluid.name}", 190), ln=True)
        pdf.cell(0, 7, txt=f"Общий объем системы: {V} м3 | Итоговая смета на интервал: {cost:,.0f} руб.", ln=True)
        pdf.ln(4)

        pdf.set_fill_color(230, 230, 230)
        pdf.set_font(pdf.font_family, size=10)

        pdf.cell(0, 7, txt="Целевые технологические параметры (ТЗ для буровой):", border=1, ln=True, fill=True)
        pdf.cell(95, 7, txt=safe_text(f"Требуемая плотность: {it.req_density_min} - {it.req_density_max} г/см3", 93),
                 border=1)
        pdf.cell(95, 7, txt=safe_text(f"Пластическая вязкость: {target_props['viscosity']} мПа*с", 93), border=1,
                 ln=True)
        pdf.cell(95, 7, txt=safe_text(f"ДНС (для угла {it.angle} град.): {target_props['tau_0']} дПа", 93), border=1)
        pdf.cell(95, 7, txt=safe_text(f"Показатель фильтрации (макс): < {target_props['filtration']} см3", 93),
                 border=1, ln=True)
        pdf.ln(4)

        pdf.set_font(pdf.font_family, size=11)
        pdf.cell(0, 7, txt="Исходные данные (Введенные параметры скважины):", ln=True)
        pdf.set_font(pdf.font_family, size=10)

        pdf.cell(95, 5, txt=f"- Диаметр долота: {it.diameter} мм")
        pdf.cell(95, 5, txt=f"- Температура забоя: {it.t_zab} C", ln=True)
        pdf.cell(95, 5, txt=f"- Пластовое давление: {it.p_pl} МПа")
        pdf.cell(95, 5, txt=f"- Профиль ствола: {it.well_profile} (Угол {it.angle} град.)", ln=True)

        pdf.cell(0, 5, txt=safe_text(f"- Тип интервала: {it.interval_type}", 190), ln=True)
        pdf.cell(0, 5, txt=safe_text(f"- Осложнения: {it.complications_list or 'Нет осложнений'}", 190), ln=True)
        pdf.ln(4)

        if pdf.get_y() > 255:
            pdf.add_page()

        pdf.set_font(pdf.font_family, size=11)
        pdf.cell(0, 7, txt="Требуемые химические реагенты (Инструкция для склада):", ln=True)

        col_w = [60, 50, 25, 30, 25]

        pdf.set_font(pdf.font_family, size=8)
        pdf.cell(col_w[0], 7, txt="Наименование", border=1, align='C', fill=True)
        pdf.cell(col_w[1], 7, txt="Назначение", border=1, align='C', fill=True)
        pdf.cell(col_w[2], 7, txt="кг/м3", border=1, align='C', fill=True)
        pdf.cell(col_w[3], 7, txt="Потребность", border=1, align='C', fill=True)
        pdf.cell(col_w[4], 7, txt="Руб", border=1, ln=True, align='C', fill=True)

        pdf.set_font(pdf.font_family, size=9)
        for r in recipe:
            total_mass = round(r['mass'] * V, 1)
            unit = "л" if r['name'] in ["Нефтяная основа (РУО)", "Вода техническая"] else "кг"

            if total_mass > 1000 and unit == "кг":
                total_mass = round(total_mass / 1000, 2)
                unit = "т"

            if pdf.get_y() > 270:
                pdf.add_page()

            pdf.set_x(10)
            pdf.cell(col_w[0], 7, txt=safe_text(r['name'], col_w[0] - 2), border=1)
            pdf.cell(col_w[1], 7, txt=safe_text(r['func'], col_w[1] - 2), border=1)
            pdf.cell(col_w[2], 7, txt=safe_text(r['mass'], col_w[2] - 2), border=1, align='C')
            pdf.cell(col_w[3], 7, txt=safe_text(f"{total_mass} {unit}", col_w[3] - 2), border=1, align='C')
            pdf.cell(col_w[4], 7, txt=safe_text(f"{r['price']:,.0f}", col_w[4] - 2), border=1, ln=True, align='R')

        pdf.ln(4)

        is_productive = 'Продуктивный' in it.interval_type
        fluids_alt = Fluid.objects.filter(temp_max__gte=it.t_zab, density_max__gte=it.req_density_min,
                                          density_min__lte=rho_max, is_drill_in_fluid=is_productive)
        if fluids_alt.exists():
            weights_alt = {
                'weight_filtration': it.final_w_filtration, 'weight_inhibition': it.final_w_inhibition,
                'weight_friction': it.final_w_friction, 'weight_eco': it.final_w_eco, 'weight_cost': it.final_w_cost
            }
            fluid_list_alt = []
            for f in fluids_alt:
                f_dict = f.__dict__.copy()
                _, _, _, dyn_cost_m3 = get_recipe_data(f.id, it.req_density_min, it.diameter, it.depth, it.t_zab,
                                                       it.complications_list or "")
                f_dict['cost'] = dyn_cost_m3
                fluid_list_alt.append(f_dict)

            df_alt = pd.DataFrame(fluid_list_alt)
            df_alt['rating'] = calculate_topsis(df_alt, weights_alt)

            best_row_alt = df_alt.sort_values('rating', ascending=False).iloc[0]
            best_cost_alt = best_row_alt['cost']
            top3_df_alt = df_alt.sort_values('rating', ascending=False).head(3).iloc[1:]

            if not top3_df_alt.empty:
                if pdf.get_y() > 265:
                    pdf.add_page()

                pdf.set_font(pdf.font_family, size=11)
                pdf.cell(0, 7, txt="Альтернативные технологические решения (TOPSIS):", ln=True)
                pdf.set_font(pdf.font_family, size=10)

                place_alt = 2
                for _, row in top3_df_alt.iterrows():
                    reasons_alt = []
                    if row['cost'] > best_cost_alt * 1.05:
                        reasons_alt.append(f"дороже на {int(row['cost'] - best_cost_alt)} руб/м3")
                    if row['eco_score'] < best_row_alt['eco_score']:
                        reasons_alt.append("ниже экологичность")
                    if row['filtration'] > best_row_alt['filtration']:
                        reasons_alt.append("выше водоотдача")
                    if row['inhibition'] < best_row_alt['inhibition']:
                        reasons_alt.append("хуже ингибирование")

                    reason_str_alt = ", ".join(reasons_alt[:2]) if reasons_alt else "минимальное отставание по весам"

                    text_line = (f"- Вариант #{place_alt}: {row['name']} ({reason_str_alt.capitalize()}). "
                                 f"Плотн: {row['density_min']}-{row['density_max']} г/см3, "
                                 f"Макс. темп: {row['temp_max']}C, "
                                 f"Стоимость: {round(row['cost'], 0):,.0f} руб/м3")

                    if pdf.get_y() > 275:
                        pdf.add_page()

                    pdf.set_x(10)
                    pdf.multi_cell(190, 5, txt=text_line)
                    place_alt += 1

        pdf.ln(10)

    if pdf.get_y() > 260:
        pdf.add_page()

    pdf.set_font(pdf.font_family, size=10)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(5)

    pdf.cell(0, 6, txt="Сгенерировано в системе: SmartMud Expert", ln=True)
    pdf.ln(8)

    pdf.cell(100, 6, txt=safe_text(f"Составил (Инженер): {user.full_name} _________", 98))
    pdf.cell(90, 6, txt="Утвердил (Главный технолог): ___________________", ln=True)

    pdf_bytes = pdf.output()
    response = HttpResponse(bytes(pdf_bytes), content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="Mud_Program_{project.id}.pdf"'
    return response


@custom_login_required
def admin_panel(request):
    if request.session.get('role') != 'admin':
        return redirect('dashboard')

    if request.method == 'POST':

        if 'add_user' in request.POST:
            u = request.POST.get('new_username')
            p = request.POST.get('new_password')
            r = request.POST.get('new_role')
            hashed_p = hashlib.sha256(p.encode()).hexdigest()
            CustomUser.objects.create(username=u, password_hash=hashed_p, role=r)

        elif 'add_fluid' in request.POST:
            Fluid.objects.create(
                name=request.POST.get('f_name'),
                base_type=request.POST.get('f_base'),
                density_min=request.POST.get('f_d_min'),
                density_max=request.POST.get('f_d_max'),
                temp_max=request.POST.get('f_t_max'),
                inhibition=request.POST.get('f_inh'),
                friction=request.POST.get('f_fric'),
                eco_score=request.POST.get('f_eco'),
                cost=request.POST.get('f_cost'),
                filtration=6.0,
                base_density=1.05,
                is_drill_in_fluid=(request.POST.get('f_base') == 'Продуктивный')
            )

        elif 'add_reagent' in request.POST:
            Reagent.objects.create(
                name=request.POST.get('r_name'),
                function_type=request.POST.get('r_func'),
                target_lithology=request.POST.get('r_litho'),
                max_temp=request.POST.get('r_temp'),
                price_kg=float(request.POST.get('r_price'))
            )

        elif 'add_recipe' in request.POST:
            fluid = Fluid.objects.get(id=request.POST.get('recipe_fluid'))
            reagent = Reagent.objects.get(id=request.POST.get('recipe_reagent'))
            FluidRecipe.objects.create(
                fluid=fluid,
                reagent=reagent,
                concentration=request.POST.get('recipe_conc'),
                comment=request.POST.get('recipe_comment')
            )

        elif 'update_ahp_preset' in request.POST:
            p_id = request.POST.get('preset_selector')
            preset = LocalWeight.objects.get(id=p_id)

            def parse_saaty(val_name):
                val = request.POST.get(val_name, '1')
                if '/' in val:
                    num, den = val.split('/')
                    return float(num) / float(den)
                return float(val)

            m01 = parse_saaty('p_0_1');
            m02 = parse_saaty('p_0_2');
            m03 = parse_saaty('p_0_3');
            m04 = parse_saaty('p_0_4')
            m12 = parse_saaty('p_1_2');
            m13 = parse_saaty('p_1_3');
            m14 = parse_saaty('p_1_4')
            m23 = parse_saaty('p_2_3');
            m24 = parse_saaty('p_2_4');
            m34 = parse_saaty('p_3_4')

            matrix = np.array([
                [1.0, m01, m02, m03, m04],
                [1.0 / m01, 1.0, m12, m13, m14],
                [1.0 / m02, 1.0 / m12, 1.0, m23, m24],
                [1.0 / m03, 1.0 / m13, 1.0 / m23, 1.0, m34],
                [1.0 / m04, 1.0 / m14, 1.0 / m24, 1.0 / m34, 1.0]
            ])

            weights, cr = calculate_ahp_weights(matrix)

            if cr > 0.1:
                request.session[
                    'ahp_msg'] = f"ОШИБКА: Матрица логически противоречива (CR = {cr}). Значение должно быть < 0.1."
                request.session['ahp_status'] = "danger"
            else:
                request.session['ahp_msg'] = f"УСПЕХ: Веса рассчитаны и сохранены! CR = {cr}"
                request.session['ahp_status'] = "success"

                preset.weight_cost = weights[0]
                preset.weight_filtration = weights[1]
                preset.weight_inhibition = weights[2]
                preset.weight_friction = weights[3]
                preset.weight_eco = weights[4]
                preset.save()

            return redirect('admin_panel')

        elif 'delete_user' in request.POST:
            CustomUser.objects.filter(id=request.POST.get('delete_user')).delete()
        elif 'delete_fluid' in request.POST:
            Fluid.objects.filter(id=request.POST.get('delete_fluid')).delete()
        elif 'delete_reagent' in request.POST:
            Reagent.objects.filter(id=request.POST.get('delete_reagent')).delete()
        elif 'delete_recipe' in request.POST:
            FluidRecipe.objects.filter(id=request.POST.get('delete_recipe')).delete()

        return redirect('admin_panel')

    context = {
        'fluids': Fluid.objects.all().order_by('name'),
        'reagents': Reagent.objects.all().order_by('name'),
        'users': CustomUser.objects.all().order_by('-id'),
        'recipes': FluidRecipe.objects.all().order_by('-id'),
        'interval_presets': LocalWeight.objects.filter(category='interval').order_by('id'),
        'profile_presets': LocalWeight.objects.filter(category='profile').order_by('id'),
        'comp_presets': LocalWeight.objects.filter(category='complication').order_by('id'),
        'history': CalculationHistory.objects.all().select_related('user', 'project', 'selected_fluid').order_by(
            '-calc_date')[:50],
        'username': request.session.get('username')
    }

    context['ahp_msg'] = request.session.pop('ahp_msg', None)
    context['ahp_status'] = request.session.pop('ahp_status', None)

    return render(request, 'drilling/admin.html', context)