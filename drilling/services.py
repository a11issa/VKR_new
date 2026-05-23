# Математические расчеты

import numpy as np
import pandas as pd
from django.db.models import Q
from .models import Fluid, FluidRecipe, Reagent, LocalWeight

# Расчет окна бурения
def calculate_physics(H, D_mm, P_pl, is_gas_or_unexplored=False):
    if H <= 0 or P_pl <= 0:
        return 1.0, 2.0, 0.0, 0.0, 0.0, 0.0, "Глубина и давление должны быть больше нуля"

    rho_w = 1000 # плотность воды
    g = 9.81 # сила тяжести
    rho_g = 2300 #средняя плотность горных пород

    P_g = rho_g * g * H / 1e6 # Расчет горного давления
    P_gr = 0.87 * P_g # Расчет давления гидроразрыва пласта
    P_pogl = 0.85 * P_gr # Расчет давления поглощения

    # Суммарная репрессия на пласт

    # Шаг 2.1: Определение минимального превышения (dp_min) по Таблице 2
    dp_min = 0.0
    if H <= 1000:
        dp_min = 1.5 if is_gas_or_unexplored else 1.0
    elif 1001 <= H <= 2500:
        dp_min = 2.0 if is_gas_or_unexplored else 1.5
    elif 2501 <= H <= 4500:
        dp_min = 2.25 if is_gas_or_unexplored else 2.0
    else:
        dp_min = 2.7 if is_gas_or_unexplored else 2.5

    # Шаг 2.2: Определение надбавки на колебания при СПО (dp_spo)
    # Сначала вычисляем коэффициент аномальности (K_a)
    # Нормальное гидростатическое давление воды = 1000 * 9.81 * H / 1e6
    P_hydro = 1000 * g * H / 1e6
    k_a = P_pl / P_hydro if P_hydro > 0 else 1.0

    # Коэффициент K_spo зависит от диаметра скважины
    k_spo = 0.5 if D_mm <= 215.9 else 0.3

    dp_spo = k_spo * k_a

    # Шаг 2.3: Итоговая репрессия
    dp_sigma = dp_min + dp_spo

    # Требуемая минимальная плотность жидкости (Формула перевода давления в плотность)
    rho_req = (P_pl + dp_sigma) * 1e6 / (g * H * 1000)

    # Максимальная плотность (по давлению поглощения)
    rho_max = (P_pogl * 1e6) / (g * H * 1000)

    err = None
    if rho_req > rho_max:
        err = f"Условия несовместимы: требуемая плотность {rho_req:.2f} г/см³ превышает давление поглощения ({rho_max:.2f} г/см³). Пробурить интервал традиционным способом нельзя."

    return round(rho_req, 2), round(rho_max, 2), round(dp_sigma, 2), \
        round(P_g, 2), round(P_pogl, 2), round(P_gr,2), err

# Расчет свойств бурового раствора
def get_calculated_properties(rho_req, angle, dp_sigma):
    eta = 33 * rho_req - 22 # Требуемая пластическая вязкость
    tau_0 = 10 + 1.377 * angle # ДНС
    phi_30 = 80 / dp_sigma if dp_sigma > 0 else 80 # Допустимая водоотдача

    return {
        'viscosity': round(eta, 1),
        'tau_0': round(tau_0, 1),
        'filtration': round(phi_30, 1)
    }


def calculate_ahp_weights(matrix):
    """
    Рассчитывает веса критериев и Отношение Согласованности (CR)
    методом собственного вектора Саати.
    matrix: 5x5 numpy array
    """
    n = matrix.shape[0]

    # 1. Вычисляем собственные значения и собственные векторы (Eigenvalues & Eigenvectors)
    eigenvalues, eigenvectors = np.linalg.eig(matrix)

    # 2. Находим максимальное собственное значение (lambda_max)
    max_eigenval = np.max(np.real(eigenvalues))

    # 3. Извлекаем соответствующий собственный вектор и берем его действительную часть
    max_eigenvec = np.real(eigenvectors[:, np.argmax(np.real(eigenvalues))])

    # 4. Нормализуем вектор (чтобы сумма весов равнялась 1.0)
    weights = max_eigenvec / np.sum(max_eigenvec)

    # 5. Расчет Отношения Согласованности (CR)
    # Индекс согласованности (CI)
    ci = (max_eigenval - n) / (n - 1)

    # Случайный индекс (RI) для матрицы 5x5 по Саати равен 1.12
    ri = 1.12

    # Итоговое Отношение Согласованности (CR)
    cr = ci / ri if ri != 0 else 0

    return [round(w, 4) for w in weights], round(cr, 4)

# Нормализация данных для TOPSIS
def aggregate_and_normalize_weights(interval_type, well_profile, fluid_type, complications_list):
    # Мета-веса факторов
    W_INT = 0.10
    W_PROF = 0.30
    W_COMP = 0.60

    # Базовый нулевой вектор
    final_w = {'fil': 0.0, 'inh': 0.0, 'fric': 0.0, 'eco': 0.0, 'cost': 0.0}

    # 1. Получаем вес интервала
    try:
        ip = LocalWeight.objects.filter(category='interval', name__icontains=interval_type.split(' ')[0]).first()
        if ip:
            final_w['fil'] += ip.weight_filtration * W_INT
            final_w['inh'] += ip.weight_inhibition * W_INT
            final_w['fric'] += ip.weight_friction * W_INT
            final_w['eco'] += ip.weight_eco * W_INT
            final_w['cost'] += ip.weight_cost * W_INT
    except:
        pass

    # 2. Получаем вес профиля ствола
    try:
        pp = LocalWeight.objects.filter(category='profile', name__icontains=well_profile).first()
        if pp:
            final_w['fil'] += pp.weight_filtration * W_PROF
            final_w['inh'] += pp.weight_inhibition * W_PROF
            final_w['fric'] += pp.weight_friction * W_PROF
            final_w['eco'] += pp.weight_eco * W_PROF
            final_w['cost'] += pp.weight_cost * W_PROF
    except:
        pass

    # 3. Получаем вес осложнений (Используем метод выделения доминирующей угрозы - MAX)
    comp_vectors = []
    if complications_list:
        for comp in complications_list:
            cp = LocalWeight.objects.filter(category='complication', name__icontains=comp.split(' ')[0]).first()
            if cp: comp_vectors.append(cp)

    if comp_vectors:
        # Берем максимальную угрозу по каждому критерию из выбранных осложнений
        final_w['fil'] += max(c.weight_filtration for c in comp_vectors) * W_COMP
        final_w['inh'] += max(c.weight_inhibition for c in comp_vectors) * W_COMP
        final_w['fric'] += max(c.weight_friction for c in comp_vectors) * W_COMP
        final_w['eco'] += max(c.weight_eco for c in comp_vectors) * W_COMP
        final_w['cost'] += max(c.weight_cost for c in comp_vectors) * W_COMP

    # Нормализация (чтобы сумма была строго 1.0)
    total = sum(final_w.values())
    if total == 0: return {'weight_filtration': 0.2, 'weight_inhibition': 0.2, 'weight_friction': 0.2,
                           'weight_eco': 0.2, 'weight_cost': 0.2}

    return {
        'weight_filtration': round(final_w['fil'] / total, 3),
        'weight_inhibition': round(final_w['inh'] / total, 3),
        'weight_friction': round(final_w['fric'] / total, 3),
        'weight_eco': round(final_w['eco'] / total, 3),
        'weight_cost': round(final_w['cost'] / total, 3)
    }


# 2. Реализация метода TOPSIS с жестким контролем MIN и MAX
def calculate_topsis(df, weights_dict):
    if len(df) == 0: return []
    if len(df) == 1: return [1.0]

    # Строгий порядок столбцов: 0-Смета(MIN), 1-Фильтрация(MIN), 2-Ингибирование(MAX), 3-Трение(MAX), 4-Экология(MAX)
    matrix = df[["cost", "filtration", "inhibition", "friction", "eco_score"]].values.astype(float)

    # Векторная нормализация
    col_sums = np.sqrt((matrix ** 2).sum(axis=0))
    col_sums = np.where(col_sums == 0, 1e-10, col_sums)
    norm_matrix = matrix / col_sums

    w = np.array([
        float(weights_dict.get('weight_cost') or 0.2),
        float(weights_dict.get('weight_filtration') or 0.2),
        float(weights_dict.get('weight_inhibition') or 0.2),
        float(weights_dict.get('weight_friction') or 0.2),
        float(weights_dict.get('weight_eco') or 0.2)
    ])
    weighted_matrix = norm_matrix * w

    # Определение Идеалов (A+ и A-)
    # A+ : Cost(MIN), Filt(MIN), Inh(MAX), Fric(MAX), Eco(MAX)
    ideal_best = [
        weighted_matrix[:, 0].min(), weighted_matrix[:, 1].min(),
        weighted_matrix[:, 2].max(), weighted_matrix[:, 3].max(), weighted_matrix[:, 4].max()
    ]
    # A- : Cost(MAX), Filt(MAX), Inh(MIN), Fric(MIN), Eco(MIN)
    ideal_worst = [
        weighted_matrix[:, 0].max(), weighted_matrix[:, 1].max(),
        weighted_matrix[:, 2].min(), weighted_matrix[:, 3].min(), weighted_matrix[:, 4].min()
    ]

    dist_best = np.sqrt(((weighted_matrix - ideal_best) ** 2).sum(axis=1))
    dist_worst = np.sqrt(((weighted_matrix - ideal_worst) ** 2).sum(axis=1))

    # Избегаем деления на ноль
    denominator = dist_best + dist_worst
    denominator = np.where(denominator == 0, 1e-10, denominator)

    return dist_worst / denominator

# Расчет сметы и рецептуры
def get_recipe_data(fluid_id, req_density, D_mm, H, T_zab, complications_str):
    fluid = Fluid.objects.get(id=fluid_id)
    req_density = float(req_density)
    base_density = float(fluid.base_density)

    # Расчет объема по формуле: V_бр = V_ц.с. + K_з * V_скв
    D_m = D_mm / 1000.0
    k1 = 1.1  # Коэффициент кавернозности
    V_skv = H * (np.pi * (D_m ** 2) * k1) / 4.0 # Расчет объема скважины
    V_cs = 5.0  # Объем желобной системы
    K_z = 2.0  # Коэффициент запаса
    V_total = round(V_cs + K_z * V_skv, 1) # Итоговый объем

    fluid_base_name = "Нефтяная основа (РУО)" if fluid.base_type == "Углеводородная" else "Вода техническая"
    litho_tag = 'Общий'
    if 'глин' in complications_str.lower():
        litho_tag = 'Глины'
    elif 'трещин' in complications_str.lower() or 'поглощен' in complications_str.lower():
        litho_tag = 'Трещины'

    recipes = FluidRecipe.objects.filter(fluid=fluid, reagent__max_temp__gte=T_zab).filter(
        Q(reagent__target_lithology='Общий') | Q(reagent__target_lithology=litho_tag)
    ).select_related('reagent')

    recipe_list = []
    barite_kg = 0
    v_base_fraction = 1.0

    # Утяжеление баритом (Закон сохранения массы)
    if req_density > base_density and req_density < 4.2:
        barite_kg = int(1000 * 4.2 * (req_density - base_density) / (4.2 - req_density))
        v_barite = barite_kg / 4200.0
        v_base_fraction = 1.0 - v_barite

    cost_m3 = 0
    for r in recipes:
        corrected_conc = float(r.concentration) * v_base_fraction
        mass = int(round(corrected_conc, 0)) if corrected_conc >= 1 else round(corrected_conc, 2)

        reagent_price = r.reagent.price_kg if r.reagent.price_kg is not None else 0.0
        price = mass * float(reagent_price)

        cost_m3 += price
        recipe_list.append(
            {'name': r.reagent.name, 'func': r.reagent.function_type, 'mass': mass, 'price': price * V_total})

    if barite_kg > 0:
        try:
            barite_obj = Reagent.objects.filter(name__icontains='Барит').first()
            barite_price = float(barite_obj.price_kg) if (barite_obj and barite_obj.price_kg is not None) else 0.0
        except:
            barite_price = 0.0

        price = barite_kg * barite_price
        cost_m3 += price
        recipe_list.append({'name': 'Барит', 'func': 'Утяжелитель', 'mass': barite_kg, 'price': price * V_total})

    return V_total, recipe_list, round(cost_m3 * V_total, 2), round(cost_m3, 2)