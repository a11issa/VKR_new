# Описание базы данных

from django.db import models


class CustomUser(models.Model): # Таблица users - пользователи
    username = models.CharField(max_length=50)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(max_length=20)
    full_name = models.CharField(max_length=100)
    company = models.CharField(max_length=150)
    class Meta:
        managed = False
        db_table = 'users'


class Project(models.Model): # Таблица projects - созданные пользователями проекты
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    name = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'projects'


class LocalWeight(models.Model):
    # Таблица изолированных весов из программы МАИ
    CATEGORY_CHOICES = [
        ('interval', 'Интервал'),
        ('profile', 'Профиль ствола'),
        ('complication', 'Осложнение'),
    ]
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    name = models.CharField(max_length=100)

    # 5 цифр из МАИ (в долях от 0 до 1, сумма = 1.0)
    weight_filtration = models.FloatField(default=0.2)
    weight_inhibition = models.FloatField(default=0.2)
    weight_friction = models.FloatField(default=0.2)
    weight_eco = models.FloatField(default=0.2)
    weight_cost = models.FloatField(default=0.2)

    class Meta:
        managed = False  # Не забудь убрать False и сделать миграции, если создаешь таблицу с нуля
        db_table = 'local_weights'

class Fluid(models.Model): # Таблица fluids - справочник буровых растворов
    name = models.CharField(max_length=255)
    base_type = models.CharField(max_length=100)
    is_drill_in_fluid = models.BooleanField(default=False)
    density_min = models.FloatField()
    density_max = models.FloatField()
    temp_max = models.FloatField() #здесь лучше сделать целочисленное
    filtration = models.FloatField()
    inhibition = models.FloatField()
    friction = models.FloatField()
    eco_score = models.FloatField()
    cost = models.FloatField()
    base_density = models.FloatField()

    class Meta:
        managed = False
        db_table = 'fluids'


class Reagent(models.Model): # Таблица reagents - справочник химических реагентов
    name = models.CharField(max_length=100)
    function_type = models.CharField(max_length=100)
    target_lithology = models.CharField(max_length=100)
    max_temp = models.IntegerField()
    price_kg = models.FloatField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'reagents'


class FluidRecipe(models.Model): # Таблица fluid_recipes - рецептуры
    fluid = models.ForeignKey(Fluid, on_delete=models.CASCADE)
    reagent = models.ForeignKey(Reagent, on_delete=models.CASCADE)
    concentration = models.FloatField()
    comment = models.CharField(max_length=255, null=True, blank=True)

    class Meta:
        managed = False
        db_table = 'fluid_recipes'


class CalculationHistory(models.Model): # Таблица calculation_history - история расчетов
    project = models.ForeignKey(Project, on_delete=models.CASCADE) # (расчет именно по интервалу в какой-либо скважине, то есть проекту)
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE)
    interval_name = models.CharField(max_length=100)
    depth = models.FloatField()
    diameter = models.FloatField()
    t_zab = models.FloatField() #лучше integer
    p_pl = models.FloatField(default=0.0)
    angle = models.FloatField(default=0.0)
    req_density_min = models.FloatField()
    req_density_max = models.FloatField()
    interval_type = models.CharField(max_length=100)
    well_profile = models.CharField(max_length=100)
    complications_list = models.CharField(max_length=255, null=True, blank=True)
    final_w_filtration = models.FloatField()
    final_w_inhibition = models.FloatField()
    final_w_friction = models.FloatField()
    final_w_eco = models.FloatField()
    final_w_cost = models.FloatField()
    selected_fluid = models.ForeignKey(Fluid, on_delete=models.CASCADE) #лучше название раствора а не id
    calc_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = False
        db_table = 'calculation_history'