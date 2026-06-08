import unittest
import numpy as np
import pandas as pd
from .services import calculate_physics, get_calculated_properties, calculate_ahp_weights, calculate_topsis


class TestMathServices(unittest.TestCase):
    def test_calculate_physics_negative_values(self):
        """Тест: проверка защиты от отрицательной глубины и давления"""
        rho_req, rho_max, dp_sigma, p_g, p_pogl, p_gr, err = calculate_physics(
            H=-100, D_mm=215.9, P_pl=-5
        )
        self.assertEqual(err, "Глубина и давление должны быть больше нуля")

    def test_calculate_physics_incompatible_conditions(self):
        """Тест: проверка перехвата аварийных условий (Требуемая плотность > Допустимой)"""
        # Задаем аномально высокое пластовое давление (25 МПа) на маленькой глубине (500 м)
        rho_req, rho_max, dp_sigma, p_g, p_pogl, p_gr, err = calculate_physics(
            H=500, D_mm=215.9, P_pl=25.0
        )
        self.assertIsNotNone(err)
        self.assertTrue("превышает" in err)

    def test_calculate_physics_normal_conditions(self):
        """Тест: нормальный расчет окна бурения без ошибок"""
        rho_req, rho_max, dp_sigma, p_g, p_pogl, p_gr, err = calculate_physics(
            H=2000, D_mm=215.9, P_pl=20.0
        )
        self.assertIsNone(err)  # Ошибки быть не должно
        self.assertTrue(rho_req < rho_max)  # Минимальная плотность должна быть меньше максимальной

    # ==========================================
    # 2. ТЕСТЫ ТЕХНОЛОГИЧЕСКИХ ПАРАМЕТРОВ
    # ==========================================

    def test_get_calculated_properties(self):
        """Тест: проверка формул реологии и водоотдачи"""
        props = get_calculated_properties(rho_req=1.2, angle=30.0, dp_sigma=1.5)
        # Проверяем математику: 33 * 1.2 - 22 = 17.6
        self.assertAlmostEqual(props['viscosity'], 17.6, places=1)
        # 10 + 1.377 * 30 = 51.3
        self.assertAlmostEqual(props['tau_0'], 51.3, places=1)
        # 80 / 1.5 = 53.3
        self.assertAlmostEqual(props['filtration'], 53.3, places=1)

    # ==========================================
    # 3. ТЕСТЫ ДЛЯ МАИ (calculate_ahp_weights)
    # ==========================================

    def test_ahp_consistent_matrix(self):
        """Тест: проверка логичной (согласованной) матрицы МАИ"""
        # Если A важнее B (3), а B важнее C (2), то A должно быть важнее C.
        matrix = np.array([
            [1.0, 3.0, 5.0],
            [1 / 3, 1.0, 2.0],
            [1 / 5, 1 / 2, 1.0]
        ])
        weights, cr = calculate_ahp_weights(matrix)
        # Индекс согласованности должен быть в пределах нормы (<= 0.1)
        self.assertTrue(cr <= 0.1)
        # Сумма весов должна равняться 1
        self.assertAlmostEqual(sum(weights), 1.0, places=4)

    def test_ahp_inconsistent_matrix(self):
        """Тест: проверка бредовой (противоречивой) матрицы МАИ"""
        # Эксперт ввел противоречивые данные (A важнее B, B важнее C, но C намного важнее A)
        matrix = np.array([
            [1.0, 9.0, 1 / 9],
            [1 / 9, 1.0, 9.0],
            [9.0, 1 / 9, 1.0]
        ])
        weights, cr = calculate_ahp_weights(matrix)
        # Алгоритм должен зафиксировать сильное противоречие (CR > 0.1)
        self.assertTrue(cr > 0.1)

    # ==========================================
    # 4. ТЕСТЫ ДЛЯ TOPSIS (calculate_topsis)
    # ==========================================

    def test_topsis_ranking(self):
        """Тест: проверка правильности выбора победителя в TOPSIS"""
        # Создаем фейковую таблицу с двумя растворами
        df = pd.DataFrame([
            # Раствор 1: Дорогой, высокая фильтрация, высокое трение (ПЛОХОЙ)
            {"id": 1, "cost": 15000, "filtration": 10.0, "inhibition": 2.0, "friction": 0.8, "eco_score": 1.0},
            # Раствор 2: Дешевый, низкая фильтрация, низкое трение, высокая экология (ИДЕАЛЬНЫЙ)
            {"id": 2, "cost": 5000, "filtration": 2.0, "inhibition": 9.0, "friction": 0.1, "eco_score": 10.0}
        ])

        # Задаем равные веса для всех критериев
        weights = {
            'weight_cost': 0.2, 'weight_filtration': 0.2,
            'weight_inhibition': 0.2, 'weight_friction': 0.2, 'weight_eco': 0.2
        }

        # Получаем рейтинг
        ratings = calculate_topsis(df, weights)

        # Рейтинг идеального раствора (индекс 1) должен быть ВЫШЕ, чем у плохого (индекс 0)
        self.assertTrue(ratings[1] > ratings[0])


if __name__ == '__main__':
    unittest.main()