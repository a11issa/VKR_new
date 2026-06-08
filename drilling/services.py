# Математические расчеты

import numpy as np
import pandas as pd
from django.db.models import Q
from .models import Fluid, FluidRecipe, Reagent, LocalWeight, BaseWeight

def calculate_physics(H, D_mm, P_pl): # Расчет гидростатических давлений и диапазона плотностей для безопасного бурения
    if H <= 0 or P_pl <= 0:
        return 1.0, 2.0, 0.0, 0.0, 0.0, 0.0, "Глубина и давление должны быть больше нуля"

    rho_w = 1000
    g = 9.81
    rho_g = 2300

    P_g = rho_g * g * H / 1e6
    P_gr = 0.87 * P_g
    P_pogl = 0.85 * P_gr

    if H <= 1200:
        dp_min = P_pl * 0.10
    else:
        dp_min = P_pl * 0.05

    P_hydro = 1000 * g * H / 1e6
    k_a = P_pl / P_hydro if P_hydro > 0 else 1.0

    k_spo = 0.5 if D_mm <= 215.9 else 0.3

    dp_spo = k_spo * k_a

    dp_sigma = dp_min + dp_spo

    rho_req = (P_pl + dp_sigma) * 1e6 / (g * H * 1000)

    rho_max = (P_pogl * 1e6) / (g * H * 1000)

    err = None
    if rho_req > rho_max:
        err = f"Несовместимые условия: Минимально требуемая плотность {rho_req:.2f} г/см³ превышает максимально допустимую ({rho_max:.2f} г/см³). Пробурить интервал традиционным способом невозможно."

    return round(rho_req, 2), round(rho_max, 2), round(dp_sigma, 2), \
        round(P_g, 2), round(P_pogl, 2), round(P_gr,2), err

# Расчет технологических параметров бурового раствора
def get_calculated_properties(rho_req, angle, dp_sigma):
    eta = 33 * rho_req - 22
    tau_0 = 10 + 1.377 * angle
    phi_30 = 80 / dp_sigma if dp_sigma > 0 else 80
    return {
        'viscosity': round(eta, 1),
        'tau_0': round(tau_0, 1),
        'filtration': round(phi_30, 1)
    }

# Реализация МАИ
def calculate_ahp_weights(matrix):
    n = matrix.shape[0]
    eigenvalues, eigenvectors = np.linalg.eig(matrix)
    max_eigenval = np.max(np.real(eigenvalues))
    max_eigenvec = np.real(eigenvectors[:, np.argmax(np.real(eigenvalues))])
    weights = max_eigenvec / np.sum(max_eigenvec)
    ci = (max_eigenval - n) / (n - 1) if n > 1 else 0
    ri_dict = {1: 0.0, 2: 0.0, 3: 0.58, 4: 0.90, 5: 1.12}
    ri = ri_dict.get(n, 1.12)
    cr = ci / ri if ri != 0 else 0
    return [round(w, 4) for w in weights], round(cr, 4)

# Агрегация весов и нормализация данных (TOPSIS)
def aggregate_and_normalize_weights(interval_type, well_profile, fluid_type, complications_list):
    base = BaseWeight.objects.first()
    if base:
        W_INT, W_PROF, W_COMP = base.weight_interval, base.weight_profile, base.weight_complication
    else:
        W_INT, W_PROF, W_COMP = 0.10, 0.30, 0.60

    final_w = {'fil': 0.0, 'inh': 0.0, 'fric': 0.0, 'eco': 0.0, 'cost': 0.0}

    ip = LocalWeight.objects.filter(category='interval', name__icontains=interval_type.split(' ')[0]).first()
    if ip:
        final_w['fil'] += ip.weight_filtration * W_INT
        final_w['inh'] += ip.weight_inhibition * W_INT
        final_w['fric'] += ip.weight_friction * W_INT
        final_w['eco'] += ip.weight_eco * W_INT
        final_w['cost'] += ip.weight_cost * W_INT

    pp = LocalWeight.objects.filter(category='profile', name__icontains=well_profile).first()
    if pp:
        final_w['fil'] += pp.weight_filtration * W_PROF
        final_w['inh'] += pp.weight_inhibition * W_PROF
        final_w['fric'] += pp.weight_friction * W_PROF
        final_w['eco'] += pp.weight_eco * W_PROF
        final_w['cost'] += pp.weight_cost * W_PROF

    comp_vectors = []
    if complications_list:
        for comp in complications_list:
            cp = LocalWeight.objects.filter(category='complication', name__icontains=comp.split(' ')[0]).first()
            if cp: comp_vectors.append(cp)

    if comp_vectors:
        final_w['fil'] += max(c.weight_filtration for c in comp_vectors) * W_COMP
        final_w['inh'] += max(c.weight_inhibition for c in comp_vectors) * W_COMP
        final_w['fric'] += max(c.weight_friction for c in comp_vectors) * W_COMP
        final_w['eco'] += max(c.weight_eco for c in comp_vectors) * W_COMP
        final_w['cost'] += max(c.weight_cost for c in comp_vectors) * W_COMP

    total = sum(final_w.values())
    if total == 0: return {'weight_filtration': 0.2, 'weight_inhibition': 0.2, 'weight_friction': 0.2, 'weight_eco': 0.2, 'weight_cost': 0.2}

    return {
        'weight_filtration': round(final_w['fil'] / total, 3), 'weight_inhibition': round(final_w['inh'] / total, 3),
        'weight_friction': round(final_w['fric'] / total, 3), 'weight_eco': round(final_w['eco'] / total, 3),
        'weight_cost': round(final_w['cost'] / total, 3)
    }


# Реализация метода TOPSIS
def calculate_topsis(df, weights_dict):
    if len(df) == 0: return []
    if len(df) == 1: return [1.0]

    matrix = df[["cost", "filtration", "inhibition", "friction", "eco_score"]].values.astype(float)

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

    ideal_best = [
        weighted_matrix[:, 0].min(), weighted_matrix[:, 1].min(),
        weighted_matrix[:, 2].max(), weighted_matrix[:, 3].min(), weighted_matrix[:, 4].max()
    ]
    ideal_worst = [
        weighted_matrix[:, 0].max(), weighted_matrix[:, 1].max(),
        weighted_matrix[:, 2].min(), weighted_matrix[:, 3].max(), weighted_matrix[:, 4].min()
    ]

    dist_best = np.sqrt(((weighted_matrix - ideal_best) ** 2).sum(axis=1))
    dist_worst = np.sqrt(((weighted_matrix - ideal_worst) ** 2).sum(axis=1))

    denominator = dist_best + dist_worst
    denominator = np.where(denominator == 0, 1e-10, denominator)

    return dist_worst / denominator

# Расчет объема системы, рецептуры и стоимости
def get_recipe_data(fluid_id, req_density, D_mm, H, T_zab, complications_str):
    fluid = Fluid.objects.get(id=fluid_id)
    req_density = float(req_density)
    base_density = float(fluid.base_density)

    D_m = D_mm / 1000.0
    k1 = 1.1
    V_skv = H * (np.pi * (D_m ** 2) * k1) / 4.0
    V_cs = 5.0
    K_z = 2.0
    V_total = round(V_cs + K_z * V_skv, 1)

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