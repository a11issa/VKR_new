from django.test import TestCase, Client
from .services import calculate_physics, get_calculated_properties


class SmartMudTests(TestCase):

    # ==========================================
    # 1. ТЕСТЫ МАТЕМАТИКИ И ЛОГИКИ (SERVICES)
    # ==========================================
    def test_geomechanics_math(self):
        """Проверяем, что геомеханические формулы работают без сбоев"""
        rho_req, rho_max, dp_sigma, p_g, p_pogl, p_gr, err = calculate_physics(
            H=2500, D_mm=215.9, P_pl=28.0, is_gas_or_unexplored=True
        )

        # Окно плотности не должно быть закрыто (ошибки быть не должно)
        self.assertIsNone(err)
        # Требуемая плотность должна быть физически логичной (больше плотности воды)
        self.assertGreater(rho_req, 1.0)
        # Требуемая плотность не должна превышать давление поглощения
        self.assertLess(rho_req, rho_max)

    def test_target_properties_calculation(self):
        """Тестируем формулы целевых свойств для PDF-регламента"""
        # Передаем искусственные исходные данные
        props = get_calculated_properties(rho_req=1.15, angle=45.0, dp_sigma=2.0)

        # Проверяем конкретные математические результаты по формулам из методички
        self.assertEqual(props['viscosity'], round(33 * 1.15 - 22, 1))
        self.assertEqual(props['tau_0'], round(10 + 1.377 * 45.0, 1))
        self.assertEqual(props['filtration'], 40.0)  # 80 / 2.0 = 40.0

    # ==========================================
    # 2. ТЕСТЫ МАРШРУТИЗАЦИИ И ИНТЕРФЕЙСА (VIEWS)
    # ==========================================
    def test_404_error_page(self):
        """Проверяем, что несуществующие страницы корректно отдают красивую 404 ошибку"""
        client = Client()
        # Имитируем переход пользователя по битой ссылке
        response = client.get('/fake-url-that-does-not-exist/')

        # Система должна вернуть HTTP-статус 404 (Not Found)
        self.assertEqual(response.status_code, 404)